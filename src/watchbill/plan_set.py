"""Pure planner: roster → fall-in :class:`Plan`.

Sequence per (host, session), in this order and no other:

1. reach the host (``status server --json``)
2. start the session if it is not running (``hosts.toml`` ``start``)
3. **attach a client viewport** — 0.8.x will not spawn ``claude --resume``
   headless (#2064)
4. reconcile shape by labels: ``workspace create --label`` / ``pane split``
   for what is missing. Pane ids come back *new*; every later step uses a
   ``{pane:<slot_id>}`` placeholder that exec resolves.
5. agents with ``agent_session`` → ``agent start <name> --kind K --pane P -- <resume argv>``
6. unref agents → ``agent start`` fresh, then prompt
7. allowlisted watchers → ``pane run``
8. ``agent prompt`` with the pinned / role / excerpt text, guarded by
   ``agent wait --until idle`` and the precondition ``status != blocked``
9. rewrite ``live_ids``; write the post-set roster (occupant guard applies)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .doctor import Probe
from .hosts import Fleet, Host
from .plan import Plan, Step, StepKind
from .plan_secure import SecureOptions, herdr_step, select
from .resume import is_verified
from .roster import Occupant, Roster

_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


@dataclass
class SetOptions:
    targets: list[str] = field(default_factory=list)
    host: str | None = None
    no_prompt: bool = False
    include_local: bool = False
    approved: bool = False
    self_pane: str | None = None
    cockpit_host: str | None = None
    live: Roster | None = None                 # a fresh roll, if available
    probes: dict[str, Probe] = field(default_factory=dict)


def agent_name(o: Occupant) -> str:
    cand = o.pane_label.lower()
    if _NAME_RE.match(cand) and not cand.startswith("p"):
        return cand
    return f"{(o.kind or 'agent')}-{o.slot_id[-6:].lower()}"


def _live_ids(live: Roster | None, o: Occupant) -> Occupant | None:
    if live is None:
        return None
    return live.by_slot(o.slot_id) or live.by_human(o.human_id)


def start_steps(plan: Plan, fleet: Fleet, host: Host, session: str, *, tag: str) -> None:
    """Start a session with the host's `start` capability, then wait for the
    server to answer. The default start command is UNVERIFIED-0.8.2."""
    argv = ["sh", "-c", host.start_cmd(session)]
    plan.add(Step(id=f"{tag}start", kind=StepKind.SHELL, host=host.name, session=session,
                  description=f"start session {session} ({host.start_cmd(session)})", argv=tuple(argv),
                  raw=tuple(argv), mutating=True, via="shell", unverified=host.start_cmd(session).startswith("herdr --session")))
    herdr_step(plan, fleet, f"{tag}up", host.name, session, "wait for the server to answer",
               "status", "server", "--json", kind=StepKind.WAIT)


def attach_steps(plan: Plan, fleet: Fleet, host: Host, session: str, *, cockpit_host: str | None,
                 self_pane: str | None, tag: str) -> None:
    """#2064: give the session a client viewport before any agent resumes.
    The viewport is a pane split off the cockpit's own Herdr pane running
    ``ssh -tt <target> -- herdr session attach <S>`` (or the local attach)."""
    cockpit = fleet.cockpit
    if cockpit is None or not self_pane:
        plan.add(Step(id=f"{tag}att", kind=StepKind.NOTE, host=host.name, session=session,
                      description="#2064: attach a client to this session by hand before agents resume "
                                  "(Watchbill is not running inside Herdr, so it cannot split a viewport)"))
        return
    key = f"viewport:{host.name}/{session}"
    herdr_step(plan, fleet, f"{tag}att1", cockpit.name, cockpit.sessions[0],
               f"split a viewport pane off the cockpit pane {self_pane} (explicit id, never --current)",
               "pane", "split", "--pane", self_pane, "--direction", "down", "--no-focus", creates=key)
    attach = host.attach_cmd(session)
    if host.transport == "local":
        cmd = ["sh", "-c", attach]
    else:
        cmd = ["ssh", "-tt", "-o", "BatchMode=yes", host.target or host.name, "--", attach]
    herdr_step(plan, fleet, f"{tag}att2", cockpit.name, cockpit.sessions[0],
               f"#2064 viewport: attach a client to {host.name}/{session}",
               "pane", "run", f"{{pane:{key}}}", *cmd, placeholders=True)


def set_steps(plan: Plan, roster: Roster, fleet: Fleet, occupants: list[Occupant], *, probes: dict[str, Probe],
              live: Roster | None, no_prompt: bool, cockpit_host: str | None, self_pane: str | None,
              tag: str = "set", assume_running: bool | None = None, skip_attach: bool = False) -> None:
    groups: dict[tuple[str, str], list[Occupant]] = {}
    for o in occupants:
        groups.setdefault((o.host, o.session), []).append(o)
    g = 0
    for (host_name, session), occs in sorted(groups.items()):
        g += 1
        host = fleet.host(host_name) or Host(name=host_name, transport="local")
        p = f"{tag}{g}."
        # 1. reach
        herdr_step(plan, fleet, f"{p}reach", host_name, session, "reach host / server status",
                   "status", "server", "--json")
        # 2. start if needed
        probe = probes.get(host_name)
        running = assume_running if assume_running is not None else (probe.running if probe else False)
        if not running:
            start_steps(plan, fleet, host, session, tag=p)
        # 3. viewport
        if not skip_attach:
            attach_steps(plan, fleet, host, session, cockpit_host=cockpit_host, self_pane=self_pane, tag=p)
        # 4. shape by labels
        shape = roster.shape_for(host_name, session)
        wanted_slots = {o.slot_id for o in occs}
        created: set[str] = set()
        if shape:
            for ws in shape.workspaces:
                panes = [pn for t in ws["tabs"] for pn in t["panes"] if pn["slot_id"] in wanted_slots]
                if not panes:
                    continue
                first = panes[0]
                live_first = _live_ids(live, roster.by_slot(first["slot_id"])) if live else None
                if live_first and live_first.live_ids.pane_id:
                    plan.notes.append(f"workspace {ws['label']} already present on {host_name}; reusing")
                else:
                    herdr_step(plan, fleet, f"{p}ws.{ws['label']}", host_name, session,
                               f"create workspace {ws['label']} (root pane → slot {first['slot_id'][-6:]})",
                               "workspace", "create", "--label", ws["label"], "--cwd", first.get("cwd") or ws.get("cwd") or "~",
                               "--no-focus", creates=first["slot_id"])
                    created.add(first["slot_id"])
                for pn in panes[1:]:
                    r0, r1 = first.get("rect") or {}, pn.get("rect") or {}
                    direction = "right" if (r1.get("x", 0) > r0.get("x", 0)) else "down"
                    herdr_step(plan, fleet, f"{p}split.{pn['slot_id'][-6:]}", host_name, session,
                               f"split pane for {pn['pane_label']} ({direction})",
                               "pane", "split", f"{{pane:{first['slot_id']}}}", "--direction", direction,
                               "--cwd", pn.get("cwd") or "~", "--no-focus", placeholders=True, creates=pn["slot_id"])
                    created.add(pn["slot_id"])
        # 5–8. occupants
        for o in occs:
            pane_tok = f"{{pane:{o.slot_id}}}" if o.slot_id in created or live is None else (
                (_live_ids(live, o) or o).live_ids.pane_id or f"{{pane:{o.slot_id}}}")
            ph = pane_tok.startswith("{")
            if o.role == "bridge":
                plan.notes.append(f"{o.human_id}: bridge (nested herdr) is never relaunched")
                continue
            if o.role == "agent":
                name = agent_name(o)
                if o.resume_argv:
                    herdr_step(plan, fleet, f"{p}{name}.start", o, session,
                               f"resume {o.kind} conversation {o.agent_session['value'][:8]}…",
                               "agent", "start", name, "--kind", o.kind or "", "--pane", pane_tok, "--", *o.resume_argv,
                               precondition="pane at interactive shell prompt", placeholders=ph,
                               unverified=not is_verified(o.kind))
                else:
                    herdr_step(plan, fleet, f"{p}{name}.start", o, session,
                               f"start fresh {o.kind} (no agent_session recorded: unref)",
                               "agent", "start", name, "--kind", o.kind or "", "--pane", pane_tok,
                               precondition="pane at interactive shell prompt", placeholders=ph)
                text = o.resume_prompt.text
                if no_prompt or not text:
                    if not no_prompt:
                        plan.notes.append(f"{o.human_id}: no resume prompt (pin one in pins.toml)")
                    continue
                herdr_step(plan, fleet, f"{p}{name}.idle", o, session, "wait until idle before prompting",
                           "agent", "wait", name, "--until", "idle", "--timeout", "90000", kind=StepKind.WAIT)
                herdr_step(plan, fleet, f"{p}{name}.prompt", o, session,
                           f"prompt ({o.resume_prompt.source}); Herdr rejects blocked agents with agent_blocked",
                           "agent", "prompt", name, text, "--wait", "--until", "working", "--timeout", "10000",
                           precondition="agent_status != blocked")
            elif o.role in ("watcher", "poller", "server"):
                if o.allow_relaunch and o.argv:
                    herdr_step(plan, fleet, f"{p}run.{o.slot_id[-6:]}", o, session, f"relaunch {o.role}: {o.cmdline[:50]}",
                               "pane", "run", pane_tok, *o.argv, placeholders=ph)
                else:
                    plan.notes.append(f"{o.human_id}: {o.role} not in allowlist; pane restored, command not relaunched")
        # 9. bookkeeping
        plan.add(Step(id=f"{p}ids", kind=StepKind.JOURNAL, host=host_name, session=session,
                      description="rewrite live_ids for restored slots", mutating=True))
    plan.add(Step(id=f"{tag}.snap", kind=StepKind.SNAP, host=cockpit_host or "cockpit",
                  description="write roster (reason=post-set); occupant guard applies to current.json", mutating=True))


def plan_set(roster: Roster, fleet: Fleet, opts: SetOptions) -> Plan:
    plan = Plan(verb="set", fleet=roster.fleet, approved=opts.approved)
    sel = SecureOptions(targets=opts.targets, host=opts.host, include_local=opts.include_local,
                        cockpit_host=opts.cockpit_host, self_pane=opts.self_pane)
    occupants, refusals, notes = select(roster, sel)
    plan.refusals.extend(refusals)
    plan.notes.extend(notes)
    if not occupants:
        plan.notes.append("nothing selected")
        return plan
    set_steps(plan, roster, fleet, occupants, probes=opts.probes, live=opts.live, no_prompt=opts.no_prompt,
              cockpit_host=opts.cockpit_host, self_pane=opts.self_pane)
    return plan
