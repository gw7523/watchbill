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

from .hosts import Fleet, Host
from .plan import Plan, Refusal, Step, StepKind, is_mutating
from .roster import Occupant, Roster
from .transport import make_session

MODES = ("detach", "park", "fold", "dismiss", "host")

# What "park" types into an agent. Claude is verified (/exit). Others follow
# their documented slash commands; marked unverified until probed live.
PARK_INPUT: dict[str, tuple[str, bool]] = {
    "claude": ("/exit", True), "grok": ("/exit", False), "codex": ("/quit", False), "gemini": ("/quit", False),
    "cursor": ("/exit", False), "opencode": ("/exit", False), "hermes": ("/exit", False),
}
INTERRUPT_KEY = "ctrl-c"   # UNVERIFIED-0.8.2 key name for send-keys; `esc` is the documented example.


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


def _hs_argv(fleet: Fleet, host_name: str, session: str, *args: str) -> tuple[list[str], tuple[str, ...]]:
    host = fleet.host(host_name) or Host(name=host_name, transport="local", cockpit=False)
    hs = make_session(host, session)
    return hs.herdr_argv(*args), tuple(args)


def herdr_step(plan: Plan, fleet: Fleet, sid: str, occ_or_host, session: str, description: str, *args: str,
               kind: StepKind = StepKind.HERDR, slot_id: str | None = None, human_id: str | None = None,
               precondition: str | None = None, placeholders: bool = False, creates: str | None = None,
               unverified: bool = False) -> Step:
    host_name = occ_or_host.host if isinstance(occ_or_host, Occupant) else occ_or_host
    if isinstance(occ_or_host, Occupant):
        slot_id, human_id = occ_or_host.slot_id, occ_or_host.human_id
    argv, raw = _hs_argv(fleet, host_name, session, *args)
    step = Step(id=sid, kind=kind, host=host_name, session=session, description=description, argv=tuple(argv),
                mutating=is_mutating(("herdr", *raw)), slot_id=slot_id, human_id=human_id, precondition=precondition,
                placeholders=placeholders, raw=raw, creates=creates, unverified=unverified, via="herdr")
    return plan.add(step)


def select(roster: Roster, opts: SecureOptions) -> tuple[list[Occupant], list[Refusal], list[str]]:
    """Resolve targets to occupants. Empty targets = whole fleet (minus cockpit)."""
    refusals: list[Refusal] = []
    notes: list[str] = []
    if not opts.targets:
        pool = [o for o in roster.occupants if opts.host is None or o.host == opts.host]
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
        if o.host == cockpit_host and self_pane and pid == self_pane:
            plan.notes.append(f"skip self pane {o.human_id} ({pid})")
            continue
        if o.role == "bridge":
            plan.notes.append(f"skip bridge {o.human_id}: nested herdr is never parked or relaunched")
            continue
        if o.role in ("shell", "editor"):
            continue
        if o.role == "agent":
            if o.agent_status == "blocked":
                plan.refusals.append(Refusal("agent is blocked on an approval/question dialog; answer it by hand first",
                                             host=o.host, human_id=o.human_id, slot_id=o.slot_id))
                continue
            if o.agent_status == "working" and not force:
                plan.refusals.append(Refusal("agent is working", host=o.host, human_id=o.human_id,
                                             slot_id=o.slot_id, override="--force"))
                continue
            text, verified = PARK_INPUT.get(o.kind or "", ("/exit", False))
            n += 1
            herdr_step(plan, fleet, f"{prefix}{n}a", o, o.session, f"type {text} into {o.kind}",
                       "pane", "send-text", pid, text, unverified=not verified)
            herdr_step(plan, fleet, f"{prefix}{n}b", o, o.session, "press enter", "pane", "send-keys", pid, "enter",
                       unverified=True)   # key name: only `esc` is documented
            herdr_step(plan, fleet, f"{prefix}{n}c", o, o.session, "wait for the agent to exit (pane back at shell)",
                       "agent", "wait", pid, "--until", "unknown", "--timeout", "20000", kind=StepKind.WAIT)
            parked.append(o)
        elif o.role in ("watcher", "poller", "server"):
            n += 1
            herdr_step(plan, fleet, f"{prefix}{n}a", o, o.session, f"interrupt {o.role} ({o.cmdline[:40]})",
                       "pane", "send-keys", pid, INTERRUPT_KEY, unverified=True)
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
    if opts.mode == "fold":
        wss = {(o.host, o.session, o.live_ids.workspace_id, o.workspace_label) for o in parked}
        for i, (h, s, wsid, label) in enumerate(sorted(wss, key=lambda x: (x[0], x[1], x[2] or "")), 1):
            herdr_step(plan, fleet, f"fold{i}", h, s, f"close workspace {label}", "workspace", "close", wsid or "?")
    elif opts.mode in ("dismiss", "host"):
        for i, ((h, s), _o) in enumerate(sorted(touched.items()), 1):
            herdr_step(plan, fleet, f"stop{i}", h, s, f"stop session {s} (not server stop)", "session", "stop", s)
    if not occupants:
        plan.notes.append("nothing selected")
    return plan
