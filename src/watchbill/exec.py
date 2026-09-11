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
                 session_factory=make_session, local_runner=subprocess.run, out=print):
        self.fleet = fleet
        self.journal = journal
        self.run_id = run_id
        self.force_server_stop = force_server_stop
        self.session_factory = session_factory
        self.local_runner = local_runner
        self.out = out
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

    def _record_created(self, step: Step, res: CmdResult, pane_map: dict[str, str]) -> None:
        if not step.creates or not res.ok:
            return
        try:
            r = res.json()
        except ValueError:
            return
        pid = (r.get("root_pane") or {}).get("pane_id") or (r.get("pane") or {}).get("pane_id")
        if pid:
            pane_map[step.creates] = pid
        wsid = (r.get("workspace") or {}).get("workspace_id")
        if wsid:
            pane_map[f"ws:{step.creates}"] = wsid

    def _check_precondition(self, step: Step, hs: HostSession, raw: tuple[str, ...]) -> str | None:
        """Pitfall 12: never prompt a blocked agent. Herdr rejects it too, but
        we ask first so the plan stops cleanly instead of on an error."""
        if step.precondition != "agent_status != blocked":
            return None
        target = raw[2] if len(raw) > 2 else None
        res = hs.herdr("agent", "get", target) if target else None
        if res is None or not res.ok:
            return f"cannot read agent {target} before prompting"
        try:
            status = (res.json().get("agent") or res.json()).get("agent_status")
        except ValueError:
            status = None
        if status == "blocked":
            return f"agent {target} is blocked on an approval/question dialog; prompt refused"
        return None

    def _post_prompt_check(self, step: Step, hs: HostSession) -> str | None:
        """Pitfall 13: agent must still be present after a prompt."""
        target = step.raw[2] if len(step.raw) > 2 else None
        if not target:
            return None
        res = hs.herdr("agent", "get", target)
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
        for step in plan.steps:
            if step.host in failed_hosts:
                result.skipped += 1
                j.append(run_id=self.run_id, verb=plan.verb, host=step.host, step_id=step.id, status="skipped",
                         detail="earlier step on this host failed")
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
                if step.mutating or step.kind is StepKind.WAIT:
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
        if step.kind in (StepKind.SNAP, StepKind.GUARD):
            # Performed by cli._run_plan around exec (pre-* roster written and the
            # occupant guard evaluated before the first mutating step; post-set
            # snap attempted after). In the plan they are markers for dry-run.
            return None
        if step.kind is StepKind.JOURNAL:
            status = "set-complete" if step.description == "set-complete" else (
                "host-start" if step.description.startswith("host-start") else "ok")
            if status != "ok":
                self.journal.append(run_id=self.run_id, verb=plan.verb, host=step.host, step_id=step.id, status=status)
            return None
        raw = self._resolve(step.raw, result.pane_map) if step.placeholders else step.raw
        via = step.via
        if via == "none":   # hand-built steps: infer from kind
            via = {StepKind.HERDR: "herdr", StepKind.WAIT: "herdr", StepKind.SHELL: "shell", StepKind.LOCAL: "local"}.get(step.kind, "none")
        if via == "herdr":
            hs = self._hs(step.host, step.session)
            if step.precondition:
                err = self._check_precondition(step, hs, raw)
                if err:
                    return err
            res = hs.herdr(*raw)
            self._record_created(step, res, result.pane_map)
            if not res.ok:
                return res.stderr.strip() or res.stdout.strip() or f"exit {res.returncode}"
            if step.verb == ("agent", "prompt"):
                return self._post_prompt_check(step, hs)
            return None
        if via == "shell":
            hs = self._hs(step.host, step.session)
            res = hs.shell(list(raw))
            return None if res.ok else (res.stderr.strip() or f"exit {res.returncode}")
        if via == "local":
            cp = self.local_runner(list(raw), capture_output=True, text=True)
            return None if cp.returncode == 0 else (cp.stderr.strip() or f"exit {cp.returncode}")
        return f"unhandled step kind {step.kind}"


def run(plan: Plan, fleet: Fleet, journal: Journal, *, run_id: str, force_server_stop: bool = False, **kw) -> ExecResult:
    return Executor(fleet, journal, run_id=run_id, force_server_stop=force_server_stop, **kw).run(plan)
