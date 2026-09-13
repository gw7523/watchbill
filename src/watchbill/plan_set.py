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

from . import hosts as hosts_mod
from .doctor import Probe
from .hosts import Fleet, Host
from .plan import Plan, Refusal, Step, StepKind
from .plan_secure import SecureOptions, backend_of, mux_step, select
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


def start_steps(plan: Plan, fleet: Fleet, host: Host, session: str, *, tag: str, first_label: str = "watchbill",
                cwd: str = "~", creates_slot: str | None = None, first_window: str | None = None) -> None:
    """Start a session. herdr: the host's `start` capability (systemd unit on
    Omarchy; the built-in default is UNVERIFIED-0.8.2). tmux: `new-session -d`
    (verified). cmux: a MANUAL relaunch followed by `restore-session`."""
    be = backend_of(fleet, host.name)
    native = be.session_start(session, first_label, cwd, first_window)
    if be.name == "cmux":
        plan.add(Step(id=f"{tag}start", kind=StepKind.MANUAL, host=host.name, session=session, mux="cmux", mutating=True,
                      description="MANUAL: launch cmux on the Mac (no CLI relaunch exists), then continue"))
        mux_step(plan, fleet, f"{tag}restore", host.name, session, "cmux restore-session (re-apply the saved layout)",
                 *be.restore_session(), unverified=True)
        return
    explicit_start = host.start != hosts_mod.DEFAULT_START
    if native and be.name == "tmux":
        # `new-session -d -s <label>` starts the server AND creates the first
        # workspace's root pane. `creates` is that occupant's slot_id so
        # {pane:<slot>} / {ws:<slot>} resolve from this one step; the shape
        # rebuild must not create the same session again (see set_steps).
        mux_step(plan, fleet, f"{tag}start", host.name, session,
                 f"start tmux server {session} with first session {first_label}",
                 *native, creates=creates_slot or f"boot:{host.name}/{session}")
    else:
        # herdr: the host's `start` (default: a detached `herdr --session S
        # server`, since the bare command stays in the foreground). A host that
        # names its own start, such as a systemd user unit, still wins.
        argv = ["sh", "-c", host.start_cmd(session)]
        plan.add(Step(id=f"{tag}start", kind=StepKind.SHELL, host=host.name, session=session, mux=host.mux,
                      description=f"start session {session} ({host.start_cmd(session)})", argv=tuple(argv),
                      raw=tuple(argv), mutating=True, via="shell"))
    up = be.server_up_poll(session, 20000) if hasattr(be, "server_up_poll") else None
    if up:
        plan.add(Step(id=f"{tag}up", kind=StepKind.WAIT, host=host.name, session=session, mux=host.mux,
                      description="wait until the server reports running:true", argv=tuple(up), raw=tuple(up),
                      mutating=False, via="shell"))
    else:
        mux_step(plan, fleet, f"{tag}up", host.name, session, "wait for the server to answer", *be.status_argv(), kind=StepKind.WAIT)


def attach_steps(plan: Plan, fleet: Fleet, host: Host, session: str, *, cockpit_host: str | None,
                 self_pane: str | None, tag: str) -> None:
    """#2064: give the session a client viewport before any agent resumes.
    The viewport is a pane split off the cockpit's own Herdr pane running
    ``ssh -tt <target> -- herdr session attach <S>`` (or the local attach)."""
    cockpit = fleet.cockpit
    be = backend_of(fleet, host.name)
    if not be.caps.needs_viewport:
        return   # tmux send-keys and cmux send need no attached client
    if cockpit is None or not self_pane or cockpit.mux != "herdr":
        plan.add(Step(id=f"{tag}att", kind=StepKind.NOTE, host=host.name, session=session,
                      description="#2064: attach a client to this session by hand before agents resume "
                                  "(Watchbill is not running inside Herdr, so it cannot split a viewport)"))
        return
    key = f"viewport:{host.name}/{session}"
    cbe = backend_of(fleet, cockpit.name)
    mux_step(plan, fleet, f"{tag}att1", cockpit.name, cockpit.sessions[0],
             f"split a viewport pane off the cockpit pane {self_pane} (explicit id, never --current)",
             *cbe.pane_split(self_pane, "down", "~"), creates=key)
    attach = host.attach_cmd(session)
    if host.transport == "local":
        cmd = ["sh", "-c", attach]
    else:
        cmd = ["ssh", "-tt", "-o", "BatchMode=yes", host.target or host.name, "--", attach]
    mux_step(plan, fleet, f"{tag}att2", cockpit.name, cockpit.sessions[0],
             f"#2064 viewport: attach a client to {host.name}/{session}",
             *cbe.pane_run(f"{{pane:{key}}}", cmd, "~"), placeholders=True)


def set_steps(plan: Plan, roster: Roster, fleet: Fleet, occupants: list[Occupant], *, probes: dict[str, Probe],
              live: Roster | None, no_prompt: bool, cockpit_host: str | None, self_pane: str | None,
              tag: str = "set", assume_running: bool | None = None, skip_attach: bool = False,
              boot_slot: str | None = None) -> None:
    groups: dict[tuple[str, str], list[Occupant]] = {}
    for o in occupants:
        groups.setdefault((o.host, o.session), []).append(o)
    g = 0
    for (host_name, session), occs in sorted(groups.items()):
        g += 1
        host = fleet.host(host_name) or Host(name=host_name, transport="local")
        be = backend_of(fleet, host_name)
        p = f"{tag}{g}."
        # 1. reach the host. A transport check, not the mux's status verb: the
        #    server may legitimately be down here (that is what step 2 fixes), and
        #    tmux's status verb fails outright on a dead server.
        plan.add(Step(id=f"{p}reach", kind=StepKind.SHELL, host=host_name, session=session, mux=host.mux,
                      description="reach host", argv=("sh", "-c", "true"), raw=("sh", "-c", "true"),
                      mutating=False, via="shell"))
        # 2. start if needed
        probe = probes.get(host_name)
        running = assume_running if assume_running is not None else (probe.running if probe else False)
        shape = roster.shape_for(host_name, session)
        wanted = {o.slot_id for o in occs}
        first_ws = next((w for w in (shape.workspaces if shape else [])
                         if any(pn["slot_id"] in wanted for t in w["tabs"] for pn in t["panes"])), None)
        booted_slot = boot_slot if boot_slot is not None else None
        if not running:
            # tmux: `new-session` both starts the server and makes the first
            # workspace's root pane, so that pane's slot is created here.
            if be.name == "tmux" and first_ws:
                for t in first_ws["tabs"]:
                    hit = [pn for pn in t["panes"] if pn["slot_id"] in wanted]
                    if hit:
                        booted_slot = hit[0]["slot_id"]
                        break
            first_tab = next((t for t in (first_ws or {}).get("tabs", [])
                              if any(pn["slot_id"] in wanted for pn in t["panes"])), None)
            start_steps(plan, fleet, host, session, tag=p, first_label=(first_ws or {}).get("label", "watchbill"),
                        cwd=(first_ws or {}).get("cwd") or "~", creates_slot=booted_slot,
                        first_window=(first_tab or {}).get("label"))
        # 3. viewport
        if not skip_attach:
            attach_steps(plan, fleet, host, session, cockpit_host=cockpit_host, self_pane=self_pane, tag=p)
        # 4. shape by labels
        wanted_slots = wanted
        created: set[str] = set()
        if booted_slot and booted_slot in wanted_slots:
            plan.notes.append(f"{host_name}: first workspace root pane comes from the tmux server start (boot:{host_name}/{session})")
        if shape:
            for ws in shape.workspaces:
                tabs = [(t, [pn for pn in t["panes"] if pn["slot_id"] in wanted_slots]) for t in ws["tabs"]]
                tabs = [(t, panes) for t, panes in tabs if panes]
                if not tabs:
                    continue
                ws_key = tabs[0][1][0]["slot_id"]      # workspace id placeholder key = first restored slot
                live_first = _live_ids(live, roster.by_slot(ws_key)) if live else None
                ws_live = bool(live_first and live_first.live_ids.pane_id)
                if ws_live:
                    plan.notes.append(f"workspace {ws['label']} already present on {host_name}; reusing")
                for ti, (t, panes) in enumerate(tabs):
                    first = panes[0]
                    if ws_live:
                        pass
                    elif first["slot_id"] == booted_slot:
                        created.add(first["slot_id"])   # placeholder filled by the start step
                    elif ti == 0:
                        ws_argv = (be.workspace_create(ws["label"], first.get("cwd") or ws.get("cwd") or "~", t["label"])
                                   if be.name == "tmux" else
                                   be.workspace_create(ws["label"], first.get("cwd") or ws.get("cwd") or "~"))
                        mux_step(plan, fleet, f"{p}ws.{ws['label']}", host_name, session,
                                 f"create workspace {ws['label']} (root pane → slot {first['slot_id'][-6:]})",
                                 *ws_argv, creates=first["slot_id"], unverified=be.caps.docs_only)
                        if be.name == "cmux":
                            plan.add(Step(id=f"{p}ws.{ws['label']}.note", kind=StepKind.NOTE, host=host_name, session=session,
                                          mux="cmux", description=f"cmux `new-workspace` takes no label or cwd, so "
                                                                  f"{ws['label']!r} comes back untitled; rename it in the app (⌘⇧R)"))
                        created.add(first["slot_id"])
                    else:
                        tab_argv = be.tab_create(f"{{ws:{ws_key}}}", t["label"], first.get("cwd") or "~")
                        if tab_argv is None:
                            plan.notes.append(f"{host_name}: {be.name} has no tab create; panes of tab {t['label']} split off the first pane")
                            continue
                        mux_step(plan, fleet, f"{p}tab.{ws['label']}.{t['label']}", host_name, session,
                                 f"create tab {t['label']} in {ws['label']} (root pane → slot {first['slot_id'][-6:]})",
                                 *tab_argv, placeholders=True, creates=first["slot_id"])
                        created.add(first["slot_id"])
                    # herdr: the `splits` tree format is UNVERIFIED-0.8.2, so each extra pane splits
                    # off the tab's first pane by rect position. tmux: the exact layout string is
                    # re-applied afterwards (select-layout), so split direction only needs to be sane.
                    for pn in panes[1:]:
                        r0, r1 = first.get("rect") or {}, pn.get("rect") or {}
                        direction = "right" if (r1.get("x", 0) > r0.get("x", 0)) else "down"
                        mux_step(plan, fleet, f"{p}split.{pn['slot_id'][-6:]}", host_name, session,
                                 f"split pane for {pn['pane_label']} ({direction})",
                                 *be.pane_split(f"{{pane:{first['slot_id']}}}", direction, pn.get("cwd") or "~"),
                                 placeholders=True, creates=pn["slot_id"], unverified=be.caps.docs_only)
                        created.add(pn["slot_id"])
                    restored_all = len(panes) == len(t["panes"])
                    if len(panes) > 1 and restored_all and t.get("layout") and be.caps.layout_reapply and not ws_live:
                        lay = be.layout_apply(f"{ws['label']}:{t['label']}", t["layout"])
                        if lay:
                            mux_step(plan, fleet, f"{p}layout.{ws['label']}.{t['label']}", host_name, session,
                                     f"re-apply the recorded {be.name} layout for {ws['label']}:{t['label']}", *lay)
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
                target = be.agent_target(pane_tok, name)
                if o.resume_argv:
                    how = (f"resume {o.kind} conversation {o.agent_session['value'][:8]}…" if o.agent_session
                           else f"resume {o.kind} via {' '.join(o.resume_argv)} (cwd-scoped)")
                    for j, argv in enumerate(be.agent_start(name, o.kind or "", pane_tok, o.resume_argv)):
                        mux_step(plan, fleet, f"{p}{name}.start" + (f".{j}" if j else ""), o, session, how,
                                 *argv, precondition="pane at interactive shell prompt", placeholders=ph,
                                 unverified=(not is_verified(o.kind)) or be.caps.docs_only)
                else:
                    why = "no agent_session recorded: unref" if be.caps.native_resume else "no unambiguous continue form"
                    for j, argv in enumerate(be.agent_start(name, o.kind or "", pane_tok, None)):
                        mux_step(plan, fleet, f"{p}{name}.start" + (f".{j}" if j else ""), o, session,
                                 f"start fresh {o.kind} ({why})", *argv,
                                 precondition="pane at interactive shell prompt", placeholders=ph, unverified=be.caps.docs_only)
                text = o.resume_prompt.text
                if no_prompt or not text:
                    if not no_prompt:
                        plan.notes.append(f"{o.human_id}: no resume prompt (pin one in pins.toml)")
                    continue
                tph = target.startswith("{")   # herdr targets by agent name; tmux/cmux by (placeholder) pane id
                mux_step(plan, fleet, f"{p}{name}.idle", o, session, "wait until idle before prompting",
                         *be.agent_wait_idle(target, 90000), kind=StepKind.WAIT,
                         placeholders=tph, unverified=be.caps.agent_status != "native")
                if be.name != "herdr":
                    # send-keys -l of a multi-line string submits at every newline.
                    text = " ".join(ln.strip() for ln in text.splitlines() if ln.strip())
                for j, argv in enumerate(be.agent_prompt(target, text)):
                    mux_step(plan, fleet, f"{p}{name}.prompt" + (f".{j}" if j else ""), o, session,
                             f"prompt ({o.resume_prompt.source}); " + ("Herdr rejects blocked agents with agent_blocked"
                                                                    if be.name == "herdr" else "blocked check is heuristic (screen patterns)"),
                             *argv, precondition="agent_status != blocked", placeholders=tph)
            elif o.role in ("watcher", "poller", "server"):
                if o.allow_relaunch and o.argv:
                    mux_step(plan, fleet, f"{p}run.{o.slot_id[-6:]}", o, session, f"relaunch {o.role}: {o.cmdline[:50]}",
                             *be.pane_run(pane_tok, o.argv, o.effective_cwd or "~"), placeholders=ph)
                    if be.name == "cmux":
                        mux_step(plan, fleet, f"{p}run.{o.slot_id[-6:]}.enter", o, session, "press enter",
                                 *be.send_enter(pane_tok), placeholders=ph, unverified=True)
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
