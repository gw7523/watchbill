"""Pure planner: compose secure → action → set into a maintenance window.

Cold (default; the only mode a pacman/Omarchy host can use)::

    1. snap (pre-relieve), occupant guard
    2. park the action's declared roles
    3. copy session.json aside (#3415 persist.clear race)
    4. session stop  — only if the blast radius says so
    5. action commands
    6. doctor + optional --expected-version
    7. start + attach if the session was stopped
    8. set the affected slots
    9. journal; next host (--rolling default)
   10. --resume skips hosts already marked set-complete

Live (``--mode live``): only when doctor says ``handoff: supported`` for
every host in scope. Otherwise :class:`RefusedPlan` — never a silent cold.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import actions as _actions
from .doctor import Probe
from .exitcodes import RefusedPlan
from .hosts import Fleet
from .plan import Plan, Refusal, Step, StepKind
from .plan_secure import herdr_step, park_steps
from .plan_set import attach_steps, set_steps, start_steps
from .roster import Roster

MODES = ("cold", "live")


@dataclass
class RelieveOptions:
    action: str
    hosts: list[str] | None = None
    mode: str = "cold"
    rolling: bool = True
    resume_done: set[str] = field(default_factory=set)
    approved: bool = False
    include_local: bool = False
    expected_version: str | None = None
    allow_reboot: bool = False
    force: bool = False
    force_server_stop: bool = False
    self_pane: str | None = None
    cockpit_host: str | None = None
    action_options: dict = field(default_factory=dict)
    probes: dict[str, Probe] = field(default_factory=dict)
    run_id: str = "run"


def session_json_path(session: str) -> str:
    return "~/.config/herdr/session.json" if session == "default" else f"~/.config/herdr/sessions/{session}/session.json"


def plan_relieve(roster: Roster, fleet: Fleet, opts: RelieveOptions) -> Plan:
    if opts.mode not in MODES:
        raise ValueError(f"unknown relieve mode {opts.mode!r}")
    ctx = _actions.ActionContext(options={**opts.action_options, "mode": opts.mode, "expected_version": opts.expected_version},
                                 probes=opts.probes)
    action = _actions.get(opts.action, ctx)
    blast = action.blast_radius()
    plan = Plan(verb="relieve", fleet=roster.fleet, mode=f"{opts.action} {opts.mode}", approved=opts.approved)
    plan.notes.append(f"blast radius: {blast.as_dict()}")
    plan.notes.append("rolling: one host at a time" if opts.rolling else "no-rolling: hosts in sequence without per-host settle")

    host_names = opts.hosts or [h.name for h in fleet.hosts]
    # Live mode is all-or-nothing: refuse before planning anything.
    if opts.mode == "live":
        for hn in host_names:
            p = opts.probes.get(hn)
            if p is None or not p.handoff_supported:
                why = "no probe" if p is None else f"flavor={p.flavor} live_handoff_flag={p.live_handoff_flag}"
                raise RefusedPlan(f"{hn}: live handoff unsupported ({why}); run --mode cold instead")

    plan.add(Step(id="snap", kind=StepKind.SNAP, host=opts.cockpit_host or "cockpit",
                  description="write roster (reason=pre-relieve)", mutating=True))
    plan.add(Step(id="guard", kind=StepKind.GUARD, host=opts.cockpit_host or "cockpit",
                  description="occupant-count guard vs current.json"))

    for hn in host_names:
        host = fleet.host(hn)
        if host is None:
            plan.refusals.append(Refusal(f"host {hn!r} not in hosts.toml", host=hn))
            continue
        if host.cockpit and not opts.include_local:
            plan.notes.append(f"skip cockpit host {hn} (add --include-local)")
            continue
        if hn in opts.resume_done:
            plan.notes.append(f"skip {hn}: already set-complete in run {opts.run_id} (--resume)")
            continue
        probe = opts.probes.get(hn)
        if probe is None or not probe.reachable:
            plan.refusals.append(Refusal("host unreachable or not probed; run watchbill doctor", host=hn))
            continue
        if blast.allow_reboot:
            if host.cockpit:
                plan.refusals.append(Refusal("action may reboot and this is the cockpit; never reboot the cockpit", host=hn))
                continue
            if not opts.allow_reboot:
                plan.refusals.append(Refusal("action may reboot the host", host=hn, override="--allow-reboot"))
                continue
        try:
            cmds = action.commands(host, probe)
        except _actions.ActionUnavailable as exc:
            plan.refusals.append(Refusal(str(exc), host=hn))
            continue
        verify = action.verify(host)
        tag = f"{hn}."
        plan.add(Step(id=f"{tag}begin", kind=StepKind.JOURNAL, host=hn, description=f"host-start {opts.action}", mutating=True))
        # 2. park
        pool = [o for o in roster.on_host(hn) if o.role in blast.park_roles
                and (blast.park_kinds is None or o.kind in blast.park_kinds)]
        parked = park_steps(plan, fleet, pool, force=opts.force, self_pane=opts.self_pane,
                            cockpit_host=opts.cockpit_host, prefix=f"{tag}park")
        sessions = sorted({o.session for o in roster.on_host(hn)} or set(host.sessions))
        # 3. copy session.json aside
        for s in sessions:
            src = session_json_path(s)
            argv = ["sh", "-c", f"cp -p {src} {src}.watchbill-{opts.run_id} 2>/dev/null || true"]
            plan.add(Step(id=f"{tag}keep.{s}", kind=StepKind.SHELL, host=hn, session=s, argv=tuple(argv), raw=tuple(argv),
                          description="#3415: copy session.json aside before any stop", mutating=True, via="shell"))
        # 4. session stop only if declared
        if blast.needs_session_stop:
            for i, s in enumerate(sessions, 1):
                herdr_step(plan, fleet, f"{tag}stop{i}", hn, s, f"stop session {s} (blast radius; not server stop)",
                           "session", "stop", s)
        # 5. action commands
        for i, c in enumerate(cmds, 1):
            if c.via == "herdr":
                herdr_step(plan, fleet, f"{tag}act{i}", hn, sessions[0], c.description, *c.argv[1:], unverified=c.unverified)
            else:
                plan.add(Step(id=f"{tag}act{i}", kind=StepKind.SHELL, host=hn, argv=tuple(c.argv), raw=tuple(c.argv),
                              description=c.description, mutating=c.mutating, unverified=c.unverified, via="shell"))
        # 6. verify
        for i, c in enumerate(verify.commands, 1):
            if c.via == "herdr":
                herdr_step(plan, fleet, f"{tag}verify{i}", hn, sessions[0], verify.description, *c.argv[1:], kind=StepKind.WAIT)
            else:
                plan.add(Step(id=f"{tag}verify{i}", kind=StepKind.WAIT, host=hn, argv=tuple(c.argv), raw=tuple(c.argv),
                              description=verify.description, via="shell"))
        if opts.expected_version:
            plan.add(Step(id=f"{tag}expect", kind=StepKind.NOTE, host=hn,
                          description=f"expect herdr {opts.expected_version} (doctor.expect_version)"))
        # 7. start + attach if the session was stopped (even with nothing to set)
        if blast.needs_session_stop:
            for i, s in enumerate(sessions, 1):
                start_steps(plan, fleet, host, s, tag=f"{tag}set{i}.")
                attach_steps(plan, fleet, host, s, cockpit_host=opts.cockpit_host, self_pane=opts.self_pane, tag=f"{tag}set{i}.")
        elif blast.needs_client_attach and parked:
            for i, s in enumerate(sessions, 1):
                attach_steps(plan, fleet, host, s, cockpit_host=opts.cockpit_host, self_pane=opts.self_pane, tag=f"{tag}set{i}.")
        # 8. set the parked slots (shape is rebuilt from scratch after a stop)
        if parked:
            set_steps(plan, roster, fleet, parked, probes=opts.probes, live=None if blast.needs_session_stop else roster,
                      no_prompt=bool(opts.action_options.get("no_prompt")), cockpit_host=opts.cockpit_host,
                      self_pane=opts.self_pane, tag=f"{tag}slots", assume_running=True, skip_attach=True)
        # 9. journal
        plan.add(Step(id=f"{tag}done", kind=StepKind.JOURNAL, host=hn, description="set-complete", mutating=True))
    return plan
