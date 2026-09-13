"""Pure planner: roster + live facts → stand-down :class:`Plan`.

Modes::

    detach   snap only
    park     /exit (agents) or interrupt (watchers), keep the pane
    fold     park, then close the workspace
    dismiss  park, then `session stop <S>`
    host     dismiss every session on the host

Refusals (exit 3 unless the named override was given):

* an agent whose status is ``working`` — ``--force``
* a target that resolves on more than one host — ``--host``
* an occupant on the cockpit host targeted explicitly — ``--include-local``
* an agent whose status is ``blocked``: never overridable. ``/exit`` typed
  into an approval dialog is an answer, not an exit.

The pane Watchbill itself runs in (``HERDR_PANE_ID`` on the cockpit) is
always skipped. Bridges (nested herdr) are catalogued, never parked.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import mux as _mux
from .detect import status_is_parkable
from .mux.base import MANUAL, POLL
from .hosts import Fleet, Host
from .plan import Plan, Refusal, Step, StepKind
from .roster import Occupant, Roster
from .transport import make_session

MODES = ("detach", "park", "fold", "dismiss", "host")

# What "park" types into an agent. Claude is verified (/exit). Others follow
# their documented slash commands; marked unverified until probed live.
PARK_INPUT: dict[str, tuple[str, bool]] = {
    "claude": ("/exit", True), "grok": ("/exit", False), "codex": ("/quit", False), "gemini": ("/quit", False),
    "cursor": ("/exit", False), "opencode": ("/exit", False), "hermes": ("/exit", False),
}
INTERRUPT_KEY = "C-c"   # verified on 0.8.2: `ctrl-c` is rejected with invalid_key.


@dataclass
class SecureOptions:
    mode: str = "park"
    targets: list[str] = field(default_factory=list)
    host: str | None = None
    force: bool = False
    include_local: bool = False
    force_server_stop: bool = False
    approved: bool = False
    self_pane: str | None = None      # HERDR_PANE_ID on the cockpit
    cockpit_host: str | None = None


def host_of(fleet: Fleet, host_name: str) -> Host:
    return fleet.host(host_name) or Host(name=host_name, transport="local", cockpit=False)


def backend_of(fleet: Fleet, host_name: str):
    h = host_of(fleet, host_name)
    return _mux.get(h.mux, **h.mux_options)


def mux_step(plan: Plan, fleet: Fleet, sid: str, occ_or_host, session: str, description: str, *args: str,
             kind: StepKind = StepKind.HERDR, slot_id: str | None = None, human_id: str | None = None,
             precondition: str | None = None, placeholders: bool = False, creates: str | None = None,
             unverified: bool = False, planned_stop: bool = False, agent_kind: str | None = None,
             reuse: dict | None = None) -> Step:
    """Append one step whose ``raw`` is a mux CLI argv (after the backend
    prefix). Backends may hand back two markers instead of a real argv:
    ``__poll__`` (tmux: a remote shell wait loop) and ``__manual__``
    (cmux: the operator does it). Both are turned into the right Step kind
    here so planners never special-case a mux."""
    host_name = occ_or_host.host if isinstance(occ_or_host, Occupant) else occ_or_host
    if isinstance(occ_or_host, Occupant):
        slot_id, human_id = occ_or_host.slot_id, occ_or_host.human_id
        agent_kind = agent_kind or (occ_or_host.kind if occ_or_host.role == "agent" else None)
    host = host_of(fleet, host_name)
    be = backend_of(fleet, host_name)
    hs = make_session(host, session)
    raw = tuple(args)
    if raw[:1] == (MANUAL,):
        return plan.add(Step(id=sid, kind=StepKind.MANUAL, host=host_name, session=session, mux=host.mux,
                             description=f"MANUAL: {raw[1]}", mutating=True, slot_id=slot_id, human_id=human_id))
    if raw[:1] == (POLL,):
        shell = tuple(be.resolve_poll(session, raw))   # type: ignore[attr-defined]
        return plan.add(Step(id=sid, kind=StepKind.WAIT, host=host_name, session=session, mux=host.mux,
                             description=description, argv=tuple(hs.shell_argv(shell)), raw=shell, mutating=False,
                             slot_id=slot_id, human_id=human_id, via="shell", unverified=unverified,
                             placeholders=placeholders, agent_kind=agent_kind))
    argv = hs.mux_argv(*raw)
    step = Step(id=sid, kind=kind, host=host_name, session=session, description=description, argv=tuple(argv),
                mutating=be.is_mutating(raw), slot_id=slot_id, human_id=human_id, precondition=precondition,
                placeholders=placeholders, raw=raw, creates=creates, via="mux", mux=host.mux,
                planned_stop=planned_stop, agent_kind=agent_kind, reuse=reuse,
                unverified=unverified or be.caps.docs_only)   # docs-only backend: every verb is UNVERIFIED-LIVE
    return plan.add(step)


herdr_step = mux_step   # name kept for the first-pass call sites and tests


def select(roster: Roster, opts: SecureOptions) -> tuple[list[Occupant], list[Refusal], list[str]]:
    """Resolve targets to occupants. Empty targets = whole fleet (minus cockpit)."""
    refusals: list[Refusal] = []
    notes: list[str] = []
    if not opts.targets:
        pool = [o for o in roster.occupants if opts.host is None or o.host == opts.host]
        skipped_ex = [o for o in pool if o.excluded or o.ignored]
        if skipped_ex:
            notes.append(f"skipped {len(skipped_ex)} excluded/ignored occupant(s): "
                         + ", ".join(f"{o.human_id} [{o.excluded or o.ignored}]" for o in skipped_ex))
            pool = [o for o in pool if not (o.excluded or o.ignored)]
        if not opts.include_local and opts.cockpit_host:
            skipped = [o for o in pool if o.host == opts.cockpit_host]
            if skipped:
                notes.append(f"skipped {len(skipped)} occupant(s) on cockpit host {opts.cockpit_host} (add --include-local)")
            pool = [o for o in pool if o.host != opts.cockpit_host]
        return pool, refusals, notes
    chosen: dict[str, Occupant] = {}
    for target in opts.targets:
        hits = roster.resolve(target, host=opts.host)
        if not hits:
            refusals.append(Refusal(f"target {target!r} not in roster", host=opts.host))
            continue
        hosts = {o.host for o in hits}
        if len(hosts) > 1 and opts.host is None:
            refusals.append(Refusal(f"target {target!r} matches on hosts {sorted(hosts)}; pass --host", override="--host"))
            continue
        for o in hits:
            if o.excluded or o.ignored:
                which = "excluded" if o.excluded else "ignored"
                refusals.append(Refusal(f"{which} by hosts.toml ({o.excluded or o.ignored}); edit hosts.toml to act on it",
                                        host=o.host, human_id=o.human_id, slot_id=o.slot_id))
                continue
            if o.host == opts.cockpit_host and not opts.include_local:
                refusals.append(Refusal("occupant is on the cockpit host", host=o.host, human_id=o.human_id,
                                        slot_id=o.slot_id, override="--include-local"))
                continue
            chosen[o.slot_id] = o
    return list(chosen.values()), refusals, notes


def park_steps(plan: Plan, fleet: Fleet, occupants: list[Occupant], *, force: bool, self_pane: str | None,
               cockpit_host: str | None, prefix: str = "park") -> list[Occupant]:
    """Append park steps for ``occupants``; return those actually parked."""
    parked: list[Occupant] = []
    n = 0
    for o in occupants:
        pid = o.live_ids.pane_id or "?"
        if o.excluded or o.ignored:
            plan.notes.append(f"skip {'excluded' if o.excluded else 'ignored'} {o.human_id} [{o.excluded or o.ignored}]")
            continue
        if o.host == cockpit_host and self_pane and pid == self_pane:
            plan.notes.append(f"skip self pane {o.human_id} ({pid})")
            continue
        if o.role == "bridge":
            plan.notes.append(f"skip bridge {o.human_id}: nested herdr is never parked or relaunched")
            continue
        if o.role in ("shell", "editor"):
            continue
        be = backend_of(fleet, o.host)
        if o.role == "agent":
            if o.agent_status == "blocked":
                plan.refusals.append(Refusal("agent is blocked on an approval/question dialog; answer it by hand first",
                                             host=o.host, human_id=o.human_id, slot_id=o.slot_id))
                continue
            if not status_is_parkable(o.agent_status) and not force:
                why = "agent is working" if o.agent_status == "working" else \
                    f"agent status is {o.agent_status or 'unknown'} ({be.name} cannot prove it is at its prompt)"
                plan.refusals.append(Refusal(why, host=o.host, human_id=o.human_id, slot_id=o.slot_id, override="--force"))
                continue
            text, verified = PARK_INPUT.get(o.kind or "", ("/exit", False))
            n += 1
            heur = be.caps.agent_status != "native"
            clear = be.clear_input(pid)
            if clear:
                # Anything already typed would otherwise be sent together with
                # /exit as one message, and the agent would answer, not exit
                # (tmux demo, 2026-09-13).
                mux_step(plan, fleet, f"{prefix}{n}clr", o, o.session, "clear any pending input", *clear)
            mux_step(plan, fleet, f"{prefix}{n}a", o, o.session, f"type {text} into {o.kind}",
                     *be.send_text(pid, text), unverified=not verified,
                     precondition=("claude remote control disconnected" if o.kind == "claude" else None))
            mux_step(plan, fleet, f"{prefix}{n}b", o, o.session, "press enter", *be.send_enter(pid))
            mux_step(plan, fleet, f"{prefix}{n}c", o, o.session, "wait for the agent to exit (pane back at shell)",
                     *be.agent_wait_exit(pid, o.kind, 20000), kind=StepKind.WAIT, unverified=heur)
            parked.append(o)
        elif o.role in ("watcher", "poller", "server"):
            n += 1
            mux_step(plan, fleet, f"{prefix}{n}a", o, o.session, f"interrupt {o.role} ({o.cmdline[:40]})",
                     *be.interrupt(pid), unverified=(be.name == "cmux"))   # herdr/tmux C-c verified
            parked.append(o)
    return parked


def plan_secure(roster: Roster, fleet: Fleet, opts: SecureOptions) -> Plan:
    if opts.mode not in MODES:
        raise ValueError(f"unknown secure mode {opts.mode!r}")
    plan = Plan(verb="secure", fleet=roster.fleet, mode=opts.mode, approved=opts.approved)
    plan.add(Step(id="snap", kind=StepKind.SNAP, host=opts.cockpit_host or "cockpit",
                  description="write roster (reason=pre-secure)", mutating=True))
    if opts.mode == "detach":
        return plan
    occupants, refusals, notes = select(roster, opts)
    plan.refusals.extend(refusals)
    plan.notes.extend(notes)
    if opts.mode == "host":
        hosts = {o.host for o in occupants}
        occupants = [o for o in roster.occupants if o.host in hosts and (o.host != opts.cockpit_host or opts.include_local)]
    parked = park_steps(plan, fleet, occupants, force=opts.force, self_pane=opts.self_pane, cockpit_host=opts.cockpit_host)
    touched = {(o.host, o.session): o for o in occupants}
    # An excluded occupant is never taken down as a side effect: closing its
    # workspace or stopping its session would kill it just the same.
    if opts.mode == "fold":
        for o in roster.occupants:
            if o.excluded and any((o.host, o.session, o.live_ids.workspace_id) == (p.host, p.session, p.live_ids.workspace_id)
                                  for p in parked):
                plan.refusals.append(Refusal(f"closing workspace {o.workspace_label} would kill excluded {o.human_id} ({o.excluded})",
                                             host=o.host, human_id=o.human_id))
    if opts.mode in ("dismiss", "host"):
        for o in roster.occupants:
            if o.excluded and (o.host, o.session) in touched:
                plan.refusals.append(Refusal(f"stopping session {o.session} would kill excluded {o.human_id} ({o.excluded})",
                                             host=o.host, human_id=o.human_id))
            elif o.ignored and (o.host, o.session) in touched:
                plan.notes.append(f"stopping session {o.session} ENDS ignored {o.human_id} ({o.ignored}); it is not restored")
    if opts.mode == "fold":
        wss = {(o.host, o.session, o.live_ids.workspace_id, o.workspace_label) for o in parked}
        for i, (h, s, wsid, label) in enumerate(sorted(wss, key=lambda x: (x[0], x[1], x[2] or "")), 1):
            be = backend_of(fleet, h)
            mux_step(plan, fleet, f"fold{i}", h, s, f"close workspace {label}", *be.workspace_close(wsid or label))
    elif opts.mode in ("dismiss", "host"):
        for i, ((h, s), _o) in enumerate(sorted(touched.items()), 1):
            be = backend_of(fleet, h)
            stop = be.session_stop(s)
            if stop is None:
                plan.refusals.append(Refusal(f"{be.name} has no server to stop; use `secure fold` (close workspaces)", host=h))
                continue
            note = "(not server stop)" if be.name == "herdr" else "(tmux: the server IS the session)"
            mux_step(plan, fleet, f"stop{i}", h, s, f"stop session {s} {note}", *stop, planned_stop=True)
    if not occupants:
        plan.notes.append("nothing selected")
    return plan
