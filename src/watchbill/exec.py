"""The mutation boundary. Runs a :class:`Plan`; nothing else touches a live
Herdr server.

* Dry-run is the default. Only ``plan.approved`` (``--yes``) lets a mutating
  step run; unapproved mutating steps are journaled as ``dry-run``.
* Refused plans never run (exit 3). The never-emit list is re-checked here
  (``server stop`` without ``--force-server-stop``, any ``herdr machine``).
* ``{pane:<key>}`` placeholders are filled from the JSON results of the
  steps that ``creates`` them (``workspace create`` → ``root_pane.pane_id``,
  ``pane split`` → ``pane.pane_id``).
* Pitfall 13: after ``agent prompt`` the agent must still be present; an
  unexpected exit (Codex "Update available" dialog misread as idle, #3632) is
  a failure, not success.
* Hosts run in order (rolling). A failed mutating step stops that host and
  the run returns 4; earlier hosts' progress is in the journal for
  ``--resume``.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field

from .exitcodes import OK, PARTIAL, REFUSED, TRANSPORT
from .hosts import Fleet, Host
from .journal import Journal
from .plan import Plan, Step, StepKind, check_verbs_allowed
from .transport import make_session
from .transport.base import CmdResult, HostSession

_PLACEHOLDER = re.compile(r"\{(pane|ws):([^}]+)\}")


@dataclass
class ExecResult:
    code: int = OK
    ran: int = 0
    dry: int = 0
    skipped: int = 0
    failed: list[str] = field(default_factory=list)
    pane_map: dict[str, str] = field(default_factory=dict)

    def summary(self) -> str:
        return f"ran={self.ran} dry-run={self.dry} skipped={self.skipped} failed={len(self.failed)} exit={self.code}"


class Executor:
    def __init__(self, fleet: Fleet, journal: Journal, *, run_id: str, force_server_stop: bool = False,
                 session_factory=make_session, local_runner=subprocess.run, out=print, confirm=None,
                 sleep=None, prompt_settle_s: float = 5.0):
        self.fleet = fleet
        self.journal = journal
        self.run_id = run_id
        self.force_server_stop = force_server_stop
        self.session_factory = session_factory
        self.local_runner = local_runner
        self.out = out
        # MANUAL steps: the operator does something by hand (quit/relaunch cmux). `confirm(text)`
        # returns True when they say so; default reads a line from stdin, non-interactive → False.
        self.confirm = confirm or _stdin_confirm
        import time as _time
        self.sleep = sleep or _time.sleep
        self.prompt_settle_s = prompt_settle_s
        self._restored: dict[tuple[str, str], object] = {}   # (host, session) → MuxSnapshot, read once
        self._claimed: set[str] = set()                        # restored pane ids already taken
        self._sessions: dict[tuple[str, str], HostSession] = {}

    # -- plumbing ------------------------------------------------------
    def _hs(self, host_name: str, session: str | None) -> HostSession:
        key = (host_name, session or "default")
        if key not in self._sessions:
            host = self.fleet.host(host_name) or Host(name=host_name, transport="local")
            self._sessions[key] = self.session_factory(host, key[1])
        return self._sessions[key]

    def _resolve(self, raw: tuple[str, ...], pane_map: dict[str, str]) -> tuple[str, ...]:
        def sub(m: re.Match) -> str:
            key = f"{m.group(1)}:{m.group(2)}" if m.group(1) == "ws" else m.group(2)
            if key not in pane_map:
                raise KeyError(f"placeholder {m.group(0)} not created yet")
            return pane_map[key]
        return tuple(_PLACEHOLDER.sub(sub, t) for t in raw)

    def _record_created(self, step: Step, res: CmdResult, pane_map: dict[str, str], hs: HostSession) -> None:
        if not step.creates or not res.ok:
            return
        ids = hs.backend.created_ids(res.stdout)
        if ids.get("pane_id"):
            pane_map[step.creates] = ids["pane_id"]
            if step.creates.startswith("boot:"):
                pane_map.setdefault(step.creates, ids["pane_id"])
        if ids.get("workspace_id"):
            pane_map[f"ws:{step.creates}"] = ids["workspace_id"]

    def _try_reuse(self, step: Step, hs: HostSession, pane_map: dict[str, str]) -> bool:
        """Take a pane the mux already restored instead of creating a duplicate.

        Herdr restores the layout from session.json when a stopped session
        starts again (verified 2026-09-13: the restarted server already had
        workspace `rehearse`, and creating it again produced a second one).
        The restored layout is read once per host/session, after the server
        is up, and matched by workspace label, tab label and pane order. A
        restored pane is claimed at most once."""
        if not step.reuse or not step.creates:
            return False
        key = (step.host, step.session or "default")
        if key not in self._restored:
            be = hs.backend
            outs = []
            for argv in be.snapshot_argvs():
                r = hs.mux(*argv)
                if not r.ok:
                    self._restored[key] = None
                    return False
                outs.append(r.stdout)
            try:
                self._restored[key] = be.parse_snapshot(outs)
            except Exception:
                self._restored[key] = None
        snap = self._restored[key]
        if snap is None:
            return False
        ws = next((w for w in snap.workspaces if w.label == step.reuse.get("workspace")), None)
        if ws is None:
            return False
        tab = next((t for t in snap.tabs if t.workspace_id == ws.workspace_id and t.label == step.reuse.get("tab")), None)
        if tab is None:
            return False
        panes = sorted((p for p in snap.panes if p.tab_id == tab.tab_id), key=lambda p: p.index)
        i = int(step.reuse.get("index", 0))
        if i >= len(panes) or panes[i].pane_id in self._claimed:
            return False
        self._claimed.add(panes[i].pane_id)
        pane_map[step.creates] = panes[i].pane_id
        pane_map[f"ws:{step.creates}"] = ws.workspace_id
        return True

    def _run_check(self, step: Step, plan: Plan) -> str | None:
        """Compare an agent's live on-disk configuration with what was recorded
        when it was parked. Changes are reported; changes that would make the
        resumed agent wrong or stuck fail the step (and stop the host)."""
        import json as _json
        from .agentconfig import diff
        hs = self._hs(step.host, step.session)
        res = hs.shell(list(step.raw), timeout=90.0)
        live = None
        if res.ok and res.stdout.strip():
            try:
                live = _json.loads(res.stdout.strip().splitlines()[-1])
            except ValueError:
                live = None
        notes, hard = diff(step.expect, live)
        for n in notes:
            self.out(f"  config {step.human_id or step.host}: {n}")
        if notes:
            self.journal.append(run_id=self.run_id, verb=plan.verb, host=step.host, step_id=step.id, status="ok",
                                detail="config drift: " + "; ".join(notes))
        return "; ".join(hard) if hard else None

    @staticmethod
    def _pane_tail(hs: HostSession, raw: tuple[str, ...]) -> str:
        """Why did the agent not come up? The last lines of its pane usually say
        (`No conversation found`, a trust dialog, a missing binary)."""
        r = list(raw)
        flag = next((f for f in ("--pane", "--surface", "-t") if f in r and r.index(f) + 1 < len(r)), None)
        if flag is None:
            return ""
        pane = r[r.index(flag) + 1]
        argv = hs.backend.excerpt_argv(pane, 8)
        if not argv:
            return ""
        got = hs.mux(*argv)
        lines = [ln.strip() for ln in (got.stdout or "").splitlines() if ln.strip()][-4:]
        return (" | pane: " + " / ".join(lines)) if lines else ""

    RC_ACTIVE = re.compile(r"/rc active|Remote Control active")

    def _remote_control(self, step: Step, hs: HostSession, raw: tuple[str, ...]) -> str | None:
        """A Claude with Remote Control connected ignores /exit and Ctrl-C
        (Claude Code 2.1.270, macOS, 2026-09-13); a park would then wait out
        its timeout. Refuse with the reason, or, where the host opts in with
        mux_options.disconnect_remote_control = true, disconnect it through the
        /rc dialog first. Only when the screen shows it is active: /rc on a
        disconnected session would CONNECT it."""
        be = hs.backend
        pane = raw[2] if be.name == "herdr" else (raw[raw.index("-t") + 1] if "-t" in raw else None)
        argv = be.excerpt_argv(pane, 15) if pane else None
        if not argv:
            return None
        screen = hs.mux(*argv).stdout
        if not self.RC_ACTIVE.search(screen or ""):
            return None
        host = self.fleet.host(step.host)
        if not (host and host.mux_options.get("disconnect_remote_control")):
            return ("Claude Remote Control is connected, so /exit will not stop this agent. Disconnect it "
                    "(/rc → Disconnect this session) or set mux_options.disconnect_remote_control = true")
        up, enter = (("pane", "send-keys", pane, "up"), ("pane", "send-keys", pane, "enter")) if be.name == "herdr" \
            else (("send-keys", "-t", pane, "Up"), ("send-keys", "-t", pane, "Enter"))
        hs.mux(*be.send_text(pane, "/rc")); hs.mux(*enter); self.sleep(2)
        hs.mux(*up); hs.mux(*up); self.sleep(0.3); hs.mux(*enter); self.sleep(2)   # "Disconnect this session"
        if self.RC_ACTIVE.search(hs.mux(*argv).stdout or ""):
            return "tried to disconnect Claude Remote Control, but the screen still shows it active"
        self.out(f"  disconnected Claude Remote Control on {step.human_id or pane} before parking")
        return None

    def _check_precondition(self, step: Step, hs: HostSession, raw: tuple[str, ...]) -> str | None:
        """Pitfall 12: never prompt a blocked agent. Herdr rejects it too, but
        we ask first so the plan stops cleanly instead of on an error."""
        if step.precondition == "claude remote control disconnected":
            return self._remote_control(step, hs, raw)
        if step.precondition != "agent_status != blocked":
            return None
        be = hs.backend
        target = _prompt_target(be.name, raw)
        get = be.agent_get(target) if target else None
        if get is None:
            return None if be.caps.agent_status == "none" else f"cannot read agent {target} before prompting"
        res = hs.mux(*get)
        if not res.ok:
            return f"cannot read agent {target} before prompting"
        if be.name == "herdr":
            try:
                status = (res.json().get("agent") or res.json()).get("agent_status")
            except ValueError:
                status = None
            blocked = status == "blocked"
        else:
            from .detect import looks_blocked   # tmux: agent_get returns the screen
            # step.agent_kind carries the occupant's kind so the kind-specific
            # approval patterns match; without it only the generic ones would.
            blocked = looks_blocked(step.agent_kind, res.stdout)
        if blocked:
            return f"agent {target} is blocked on an approval/question dialog; prompt refused"
        return None

    def _post_prompt_check(self, step: Step, hs: HostSession, raw: tuple[str, ...] | None = None) -> str | None:
        """Pitfall 13: agent must still be present after a prompt. Uses the
        RESOLVED argv: checking `{pane:<slot>}` reported a live agent as
        vanished and skipped the Enter that submits the prompt (tmux demo)."""
        be = hs.backend
        target = _prompt_target(be.name, raw if raw is not None else step.raw)
        get = be.agent_get(target) if target else None
        if not target or get is None:
            return None
        res = hs.mux(*get)
        if not res.ok:
            return f"agent {target} vanished after prompt (unexpected exit, see #3632)"
        return None

    # -- run -------------------------------------------------------------
    def run(self, plan: Plan) -> ExecResult:
        result = ExecResult()
        j = self.journal
        violations = check_verbs_allowed(plan.steps, force_server_stop=self.force_server_stop)
        if violations:
            for v in violations:
                j.append(run_id=self.run_id, verb=plan.verb, host=None, step_id=None, status="refused", detail=v)
                self.out(f"REFUSED: {v}")
            result.code = REFUSED
            return result
        if plan.refused:
            for r in plan.refusals:
                j.append(run_id=self.run_id, verb=plan.verb, host=r.host, step_id=None, status="refused", detail=r.reason)
            self.out("plan refused; nothing executed")
            result.code = REFUSED
            return result
        scheduled = {id(s) for s in plan.scheduled()}
        failed_hosts: set[str] = set()
        self._failed_slots: dict[str, set[str]] = {}
        for step in plan.steps:
            if step.host in failed_hosts:
                result.skipped += 1
                j.append(run_id=self.run_id, verb=plan.verb, host=step.host, step_id=step.id, status="skipped",
                         detail="earlier step on this host failed")
                continue
            if step.slot_id and step.slot_id in self._failed_slots.get(step.host, set()):
                result.skipped += 1
                j.append(run_id=self.run_id, verb=plan.verb, host=step.host, step_id=step.id, status="skipped",
                         detail="an earlier step for this occupant failed", slot_id=step.slot_id)
                continue
            if id(step) not in scheduled:
                result.dry += 1
                j.append(run_id=self.run_id, verb=plan.verb, host=step.host, step_id=step.id, status="dry-run",
                         detail=" ".join(step.argv) or step.description)
                continue
            try:
                err = self._run_step(step, plan, result)
            except Exception as exc:  # transport / placeholder / local failures
                err = str(exc)
            if err:
                result.failed.append(f"{step.host}:{step.id}: {err}")
                j.append(run_id=self.run_id, verb=plan.verb, host=step.host, step_id=step.id, status="fail", detail=err)
                self.out(f"FAIL {step.host} {step.id}: {err}")
                if step.phase == "restore" and step.slot_id:
                    self._failed_slots.setdefault(step.host, set()).add(step.slot_id)
                elif step.mutating or step.kind in (StepKind.WAIT, StepKind.CHECK):
                    failed_hosts.add(step.host)
            else:
                result.ran += 1
                j.append(run_id=self.run_id, verb=plan.verb, host=step.host, step_id=step.id, status="ok")
        if result.failed:
            worked = {s.host for s in plan.steps if s.kind in (StepKind.HERDR, StepKind.SHELL, StepKind.WAIT, StepKind.LOCAL)}
            result.code = PARTIAL if worked - failed_hosts else TRANSPORT
        return result

    def _run_step(self, step: Step, plan: Plan, result: ExecResult) -> str | None:
        if step.kind in (StepKind.NOTE,):
            return None
        if step.kind is StepKind.CHECK:
            return self._run_check(step, plan)
        if step.kind is StepKind.MANUAL:
            self.out(f"MANUAL {step.host}: {step.description}")
            return None if self.confirm(step.description) else "operator did not confirm the manual step"
        if step.kind in (StepKind.SNAP, StepKind.GUARD):
            # Performed by cli._run_plan around exec (pre-* roster written and the
            # occupant guard evaluated before the first mutating step; post-set
            # snap attempted after). In the plan they are markers for dry-run.
            return None
        if step.kind is StepKind.JOURNAL:
            status = "set-complete" if step.description == "set-complete" else (
                "host-start" if step.description.startswith("host-start") else "ok")
            failed = sorted(getattr(self, "_failed_slots", {}).get(step.host, set()))
            if status == "set-complete" and failed:
                # not complete: `relieve --resume` must come back for these occupants
                self.journal.append(run_id=self.run_id, verb=plan.verb, host=step.host, step_id=step.id,
                                    status="set-partial", detail="occupants not restored: " + ", ".join(failed))
                self.out(f"  {step.host}: {len(failed)} occupant(s) not restored; host left set-partial for --resume")
                return None
            if status != "ok":
                self.journal.append(run_id=self.run_id, verb=plan.verb, host=step.host, step_id=step.id, status=status)
            return None
        raw = self._resolve(step.raw, result.pane_map) if step.placeholders else step.raw
        via = step.via
        if via == "none":   # hand-built steps: infer from kind
            via = {StepKind.HERDR: "mux", StepKind.WAIT: "mux", StepKind.SHELL: "shell", StepKind.LOCAL: "local"}.get(step.kind, "none")
        if via in ("herdr", "mux"):
            hs = self._hs(step.host, step.session)
            if step.reuse and self._try_reuse(step, hs, result.pane_map):
                self.out(f"  reuse {step.host}: {step.reuse['workspace']}/{step.reuse['tab']}#{step.reuse['index']} "
                         f"→ {result.pane_map[step.creates]} (restored by {hs.backend.name})")
                return None
            if step.precondition:
                err = self._check_precondition(step, hs, raw)
                if err:
                    return err
            res = hs.mux(*raw, timeout=step_timeout(raw, 30.0))
            if (not res.ok and step.verb == ("agent", "prompt")
                    and "agent_prompt_stalled" in (res.stderr + res.stdout)):
                # Seen on ser6 (2026-09-13): a Grok freshly resumed with a long
                # history reports idle while its TUI is still redrawing, and the
                # submitted prompt is dropped (input box empty, no state change).
                # Settle, make sure it has not become blocked, and submit once more.
                self.out(f"  prompt stalled on {step.host}; settling {self.prompt_settle_s:g}s and retrying once")
                self.sleep(self.prompt_settle_s)
                err = self._check_precondition(step, hs, raw) if step.precondition else None
                if err:
                    return err
                res = hs.mux(*raw, timeout=step_timeout(raw, 30.0))
            self._record_created(step, res, result.pane_map, hs)
            if not res.ok:
                err = res.stderr.strip() or res.stdout.strip() or f"exit {res.returncode}"
                if step.verb == ("agent", "start"):
                    err += self._pane_tail(hs, raw)
                return err
            if _is_prompt(hs.backend.name, step):
                return self._post_prompt_check(step, hs, raw)
            return None
        if via == "shell":
            hs = self._hs(step.host, step.session)
            res = hs.shell(list(raw), timeout=step_timeout(raw, 60.0))
            if step.creates:
                self._record_created(step, res, result.pane_map, hs)   # e.g. tmux new-session inside the prelude
            if res.ok:
                return None
            err = res.stderr.strip() or f"exit {res.returncode}"
            if step.kind is StepKind.WAIT and step.slot_id:
                # an agent that would not exit usually says why on screen
                m = re.search(r"agent get (\S+)|-t (\S+) -p", raw[-1] if raw else "")
                pane = next((g for g in (m.groups() if m else ()) if g), None)
                if pane:
                    err += self._pane_tail(hs, ("--pane", pane))
            return err
        if via == "local":
            cp = self.local_runner(list(raw), capture_output=True, text=True)
            return None if cp.returncode == 0 else (cp.stderr.strip() or f"exit {cp.returncode}")
        return f"unhandled step kind {step.kind}"


def step_timeout(raw: tuple[str, ...], default: float) -> float:
    """Subprocess timeout for one step, derived from the step itself so exec
    never kills a command before its own deadline: `--timeout <ms>` on a mux
    verb, or the iteration count of a `for i in $(seq 1 N)` poll loop."""
    r = list(raw)
    if "--timeout" in r and r.index("--timeout") + 1 < len(r):
        try:
            return int(r[r.index("--timeout") + 1]) / 1000 + 15
        except ValueError:
            pass
    if r[:2] == ["sh", "-c"] and len(r) > 2:
        m = re.search(r"seq 1 (\d+)\)", r[2])
        if m:
            return int(m.group(1)) * 2 + 15      # each iteration: one probe + sleep 1
    return default


def _stdin_confirm(text: str) -> bool:
    import sys
    if not sys.stdin.isatty():
        return False
    try:
        return input("  done? [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _prompt_target(mux_name: str, raw: tuple[str, ...]) -> str | None:
    """The agent target inside a prompt/send argv: herdr `agent prompt <T>`,
    tmux `send-keys -t <T>`, cmux `send --surface <T>`."""
    r = list(raw)
    if mux_name == "herdr":
        return r[2] if len(r) > 2 else None
    for flag in ("-t", "--surface"):
        if flag in r and r.index(flag) + 1 < len(r):
            return r[r.index(flag) + 1]
    return None


def _is_prompt(mux_name: str, step: Step) -> bool:
    return step.verb == ("agent", "prompt") if mux_name == "herdr" else (
        step.precondition == "agent_status != blocked" and step.raw[:1] in (("send-keys",), ("send",)))


def run(plan: Plan, fleet: Fleet, journal: Journal, *, run_id: str, force_server_stop: bool = False, **kw) -> ExecResult:
    return Executor(fleet, journal, run_id=run_id, force_server_stop=force_server_stop, **kw).run(plan)
