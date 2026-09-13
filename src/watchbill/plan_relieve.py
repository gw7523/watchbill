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
every host that will actually run. Otherwise :class:`RefusedPlan` — never a
silent cold. A live window parks nothing and stops nothing: the point of
handoff is to keep the PTYs, so only the action's ``before_stop`` commands
(``herdr update --handoff``) and verify run.
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass, field

from . import actions as _actions
from .doctor import Probe
from .exitcodes import RefusedPlan
from .hosts import Fleet
from .plan import Plan, Refusal, Step, StepKind
from .plan_secure import backend_of, mux_step, park_steps
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


def keep_session_json_body(session: str, run_id: str) -> str:
    """#3415 safety copy of the session's persisted layout, located by asking
    the target's own herdr where its socket is (session.json sits beside it).
    No home-directory assumption, so it works inside another environment and
    for named sessions alike; if herdr cannot say, it does nothing."""
    status = shlex.join(["herdr", "--session", session, "status", "server", "--json"])
    return (f"s=$({status} 2>/dev/null | sed -n 's/.*\"socket\":\"\\([^\"]*\\)\".*/\\1/p'); "
            f"[ -n \"$s\" ] && d=$(dirname \"$s\") && cp -p \"$d/session.json\" \"$d/session.json.watchbill-{run_id}\" 2>/dev/null; true")


def plan_relieve(roster: Roster, fleet: Fleet, opts: RelieveOptions) -> Plan:
    if opts.mode not in MODES:
        raise ValueError(f"unknown relieve mode {opts.mode!r}")
    hosts_in_scope = opts.hosts or [h.name for h in fleet.hosts]
    muxes = {fleet.host(h).mux for h in hosts_in_scope if fleet.host(h)}
    ctx = _actions.ActionContext(options={**opts.action_options, "mode": opts.mode, "expected_version": opts.expected_version,
                                          "mux": muxes.pop() if len(muxes) == 1 else "herdr"},
                                 probes=opts.probes)
    action = _actions.get(opts.action, ctx)
    blast = action.blast_radius()
    plan = Plan(verb="relieve", fleet=roster.fleet, mode=f"{opts.action} {opts.mode}", approved=opts.approved)
    plan.notes.append(f"blast radius: {blast.as_dict()}")
    plan.notes.append("rolling: one host at a time; a failed host stops the run, --resume continues")
    live = opts.mode == "live"

    host_names = opts.hosts or [h.name for h in fleet.hosts]
    active = [hn for hn in host_names
              if (h := fleet.host(hn)) is not None and (not h.cockpit or opts.include_local) and hn not in opts.resume_done]
    # Live mode is all-or-nothing over the hosts that will run: refuse before planning anything.
    if live:
        for hn in active:
            p = opts.probes.get(hn)
            be = backend_of(fleet, hn)
            if be.caps.live_handoff == "never":
                raise RefusedPlan(f"{hn}: {be.name} has no live handoff; run --mode cold instead")
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
        if live:
            plan.notes.append(f"{hn}: live handoff — nothing parked, session kept up")
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
        # 2. park (never in live mode: handoff keeps the PTYs)
        kinds = action.park_kinds_for(host, probe)
        pool = [] if live else [o for o in roster.on_host(hn) if o.role in blast.park_roles
                                and (kinds is None or o.kind in kinds) and not (o.excluded or o.ignored)]
        parked = park_steps(plan, fleet, pool, force=opts.force, self_pane=opts.self_pane,
                            cockpit_host=opts.cockpit_host, prefix=f"{tag}park")
        sessions = sorted({o.session for o in roster.on_host(hn)} or set(host.sessions))
        be = backend_of(fleet, hn)
        stop = blast.needs_session_stop and not live
        if stop:
            blocked_by = [o for o in roster.on_host(hn) if o.excluded and o.session in sessions]
            if blocked_by:
                for o in blocked_by:
                    plan.refusals.append(Refusal(
                        f"{opts.action} stops session {o.session}, which would kill excluded {o.human_id} ({o.excluded})",
                        host=hn, human_id=o.human_id))
                continue
            for o in roster.on_host(hn):
                if o.ignored and o.session in sessions:
                    plan.notes.append(f"{hn}: stopping session {o.session} ENDS ignored {o.human_id} ({o.ignored}); "
                                      "it is not restored")
        # 3. copy session.json aside (herdr persists; tmux does not, cmux saves on quit)
        if be.name == "herdr":
            for s in sessions:
                argv = ["sh", "-c", keep_session_json_body(s, opts.run_id)]
                plan.add(Step(id=f"{tag}keep.{s}", kind=StepKind.SHELL, host=hn, session=s, argv=tuple(argv), raw=tuple(argv),
                              description="#3415: copy session.json aside before any stop", mutating=True, via="shell"))
        # 4/5. action commands that need no running server go first, then the
        #      declared session stop, then the rest (a stopped socket answers nothing)
        def emit(cmd_list, offset):
            for i, c in enumerate(cmd_list, offset):
                if c.via == "mux":
                    # argv[0] is the backend's own binary name, replaced by the
                    # host's mux prefix. An action that means the *herdr* CLI
                    # specifically must not reach a tmux/cmux host.
                    assert c.argv[0] == be.name, f"{opts.action}: {c.argv[0]!r} command on a {be.name} host"
                    mux_step(plan, fleet, f"{tag}act{i}", hn, sessions[0], c.description, *c.argv[1:], unverified=c.unverified)
                else:
                    plan.add(Step(id=f"{tag}act{i}", kind=StepKind.SHELL, host=hn, argv=tuple(c.argv), raw=tuple(c.argv),
                                  description=c.description, mutating=c.mutating, unverified=c.unverified, via="shell"))
        early = [c for c in cmds if c.before_stop]
        late = [c for c in cmds if not c.before_stop]
        emit(early, 1)
        if stop:
            for i, s in enumerate(sessions, 1):
                stop_argv = be.session_stop(s)
                if stop_argv is None:
                    plan.add(Step(id=f"{tag}stop{i}", kind=StepKind.MANUAL, host=hn, session=s, mux=be.name, mutating=True,
                                  description=f"MANUAL: quit {be.name} on {hn} (it saves its session on quit); then continue"))
                else:
                    note = "not server stop" if be.name == "herdr" else f"{be.name}: the server is the session"
                    mux_step(plan, fleet, f"{tag}stop{i}", hn, s, f"stop session {s} (blast radius; {note})",
                             *stop_argv, planned_stop=True)
        emit(late, len(early) + 1)
        # 6. verify
        for i, c in enumerate(verify.commands, 1):
            if c.via == "mux":
                mux_step(plan, fleet, f"{tag}verify{i}", hn, sessions[0], verify.description, *c.argv[1:], kind=StepKind.WAIT)
            else:
                plan.add(Step(id=f"{tag}verify{i}", kind=StepKind.WAIT, host=hn, argv=tuple(c.argv), raw=tuple(c.argv),
                              description=verify.description, via="shell"))
        if opts.expected_version:
            plan.add(Step(id=f"{tag}expect", kind=StepKind.NOTE, host=hn,
                          description=f"expect herdr {opts.expected_version} (doctor.expect_version)"))
        # 7/8. bring it back. `set_steps` owns reach → start → attach → shape →
        # resume, so a stopped session is started exactly once: on tmux the
        # start IS the first workspace, and a second `new-session -s <label>`
        # would fail with "duplicate session".
        if parked:
            set_steps(plan, roster, fleet, parked, probes=opts.probes, live=None if stop else roster,
                      no_prompt=bool(opts.action_options.get("no_prompt")), cockpit_host=opts.cockpit_host,
                      self_pane=opts.self_pane, tag=f"{tag}set", assume_running=not stop)
        elif stop:
            # nothing to restore, but the session must come back up
            for i, s in enumerate(sessions, 1):
                shp = roster.shape_for(hn, s)
                fw = shp.workspaces[0] if shp and shp.workspaces else {}
                start_steps(plan, fleet, host, s, tag=f"{tag}set{i}.", first_label=fw.get("label", "watchbill"), cwd=fw.get("cwd") or "~")
                attach_steps(plan, fleet, host, s, cockpit_host=opts.cockpit_host, self_pane=opts.self_pane, tag=f"{tag}set{i}.")
        # 9. journal
        plan.add(Step(id=f"{tag}done", kind=StepKind.JOURNAL, host=hn, description="set-complete", mutating=True))
    return plan
