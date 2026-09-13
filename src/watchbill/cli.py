"""``watchbill`` — parse, dispatch, exit codes. No Herdr calls live here.

    watchbill roll                      catalog the fleet (read-only)
    watchbill snap [-m reason]          write a versioned roster
    watchbill secure <mode> [targets]   stand down (dry-run unless --yes)
    watchbill set [targets]             fall in (dry-run unless --yes)
    watchbill relieve <action>          secure → action → set (alias: overhaul)
    watchbill status                    live vs last roster
    watchbill doctor                    host / flavor / handoff probe

Exit codes: 0 ok · 1 partial · 2 usage · 3 refused unsafe plan · 4 transport/action error
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__, actions, collect, doctor, exitcodes, hosts, journal, paths, plan_relieve, plan_secure, plan_set, prompts, roster as _roster
from . import exec as _exec
from .classify import Allowlist
from .slots import SlotStore, new_ulid
from .transport import make_session
from .transport.base import NotImplementedInThisPass

RELIEVE_ACTIONS = tuple(actions.REGISTRY)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="watchbill", description="Watchbill: catalog, park, and restore coding-agent fleets in Herdr 0.8.2 sessions.",
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog="Exit codes: 0 ok · 1 partial · 2 usage · 3 refused · 4 transport/action error")
    p.add_argument("--version", action="version", version=f"watchbill {__version__}")
    _globals(p, argparse.SUPPRESS)
    p.set_defaults(fleet=None, hosts_file=None, host=None, json=False, probes_json=None)
    common = argparse.ArgumentParser(add_help=False)
    _globals(common, argparse.SUPPRESS)   # the same flags are accepted after the verb
    sub = p.add_subparsers(dest="cmd", metavar="<verb>", parser_class=lambda **kw: argparse.ArgumentParser(parents=[common], **kw))

    s = sub.add_parser("roll", help="catalog every occupant of every known Herdr server (read-only)")
    s.add_argument("--explain", action="store_true", help="show which classify rule fired")
    s.add_argument("--excerpts", action="store_true", help="also read the last screen lines (cockpit-local)")

    s = sub.add_parser("snap", help="roll, then write a versioned roster; retarget current.json if the guard passes")
    s.add_argument("-m", "--reason", default="manual", choices=_roster.REASONS)
    s.add_argument("--force", action="store_true", help="retarget current.json even if the occupant guard refuses")
    s.add_argument("--excerpts", action="store_true")

    s = sub.add_parser("secure", help="stand down occupants (dry-run unless --yes)")
    s.add_argument("mode", choices=plan_secure.MODES)
    s.add_argument("targets", nargs="*", help="slot_id, human_id, or workspace[/tab/pane] label; none = fleet")
    _mut_flags(s)
    s.add_argument("--force", action="store_true", help="park agents that are working")
    s.add_argument("--force-server-stop", action="store_true",
                   help="allow an UNPLANNED whole-server stop: `herdr server stop`, or a `tmux kill-server` that is not "
                        "the declared session stop of a maintenance window (never default)")

    s = sub.add_parser("set", help="fall in from a roster (dry-run unless --yes)")
    s.add_argument("targets", nargs="*")
    _mut_flags(s)
    s.add_argument("--no-prompt", action="store_true", help="resume agents but do not send resume prompts")
    s.add_argument("--from", dest="from_roster", type=Path, help="roster file (default: current.json)")

    for name in ("relieve", "overhaul"):
        s = sub.add_parser(name, help="maintenance window: secure → action → set" + (" (alias of relieve)" if name == "overhaul" else ""))
        s.add_argument("action", choices=RELIEVE_ACTIONS)
        _mut_flags(s)
        s.add_argument("--mode", choices=plan_relieve.MODES, default="cold")
        s.add_argument("--rolling", dest="rolling", action="store_true", default=True, help="one host at a time (always; kept for the contract's CLI shape)")
        s.add_argument("--resume", action="store_true", help="skip hosts marked set-complete in the last run")
        s.add_argument("--expected-version", help="doctor must see this herdr version after the action")
        s.add_argument("--allow-reboot", action="store_true", help="operator gate: let an action that may reboot proceed (never the cockpit)")
        s.add_argument("--may-reboot", action="store_true", help="custom: declare that --cmd can reboot the host")
        s.add_argument("--force", action="store_true")
        s.add_argument("--force-server-stop", action="store_true")
        s.add_argument("--no-prompt", action="store_true")
        s.add_argument("--kinds", help="upgrade-agents: comma-separated agent kinds")
        s.add_argument("--plugin", help="install-plugin: owner/repo[/subdir]")
        s.add_argument("--ref", help="install-plugin: git ref")
        s.add_argument("--startup-hooks", action="store_true", help="install-plugin: manifest has [[startup]] hooks")
        s.add_argument("--cmd", dest="custom_cmd", help="custom: command to run")  # dest != "cmd": that is the verb slot
        s.add_argument("--park", help="custom: roles to park, comma-separated")
        s.add_argument("--session-stop", action="store_true", help="custom: stop the session around the command")
        s.add_argument("--allow-partial-pacman", action="store_true", help="upgrade-herdr: allow `pacman -S herdr`")

    sub.add_parser("status", help="live roll vs last good roster")
    sub.add_parser("doctor", help="probe hosts: version, protocol, install flavor, handoff support")
    return p


def _globals(p: argparse.ArgumentParser, default) -> None:
    p.add_argument("--fleet", default=default, help="fleet name (default: from hosts.toml, else 'default')")
    p.add_argument("--hosts-file", default=default, type=Path, help="hosts.toml path (default: ~/.config/watchbill/hosts.toml)")
    p.add_argument("--host", default=default, help="restrict to one host; also disambiguates label collisions")
    p.add_argument("--json", default=default, action="store_true", help="machine-readable output")
    p.add_argument("--probes-json", default=default, type=Path, help=argparse.SUPPRESS)  # offline doctor probes


def _mut_flags(s: argparse.ArgumentParser) -> None:
    s.add_argument("--yes", action="store_true", help="execute (default is dry-run)")
    s.add_argument("--include-local", action="store_true", help="allow touching the cockpit host")


# -- helpers ---------------------------------------------------------------

def _fleet(args) -> hosts.Fleet:
    fleet = hosts.load(args.hosts_file)
    if args.fleet:
        fleet.name = args.fleet
    return fleet


def _cockpit(fleet: hosts.Fleet) -> tuple[str | None, str | None]:
    ck = fleet.cockpit
    return (ck.name if ck else hosts.local_hostname()), os.environ.get("HERDR_PANE_ID")


def _read_config() -> dict:
    import tomllib
    p = paths.config_file()
    return tomllib.loads(p.read_text()) if p.exists() else {}


def _guard_cfg(cfg: dict) -> _roster.GuardConfig:
    g = cfg.get("guard", {})
    return _roster.GuardConfig(drop_ratio=float(g.get("drop_ratio", 0.5)), min_occupants=int(g.get("min_occupants", 2)))


def _roll(fleet: hosts.Fleet, args, *, reason: str = "manual", excerpts: bool = False) -> tuple[_roster.Roster, list[str]]:
    facts, down = [], []
    for h in fleet.hosts:
        if args.host and h.name != args.host:
            continue
        hf = collect.gather_host(h, excerpts=excerpts)
        if not hf.reachable:
            down.append(f"{h.name}: {hf.error}")
        facts.append(hf)
    slots = SlotStore.load(paths.slots_file())
    allow = Allowlist.parse(paths.allowlist_file().read_text()) if paths.allowlist_file().exists() else None
    pins = prompts.Pins.parse(paths.pins_file().read_text()) if paths.pins_file().exists() else None
    templates = {}
    pdir = paths.config_dir() / "prompts"
    if pdir.is_dir():
        templates = {f.stem: f.read_text() for f in pdir.glob("*.txt")}
    cockpit_host, self_pane = _cockpit(fleet)
    ro = collect.build_roster(fleet.name, facts, slots=slots, allowlist=allow, pins=pins, templates=templates,
                              cockpit={"host": cockpit_host, "pane_id": self_pane}, reason=reason,
                              idle_after=collect.idle_after_map(fleet))
    previous = _load_current(fleet.name)
    ro = _roster.merge(ro, previous)
    slots.save(paths.slots_file())
    return ro, down


def _load_current(fleet_name: str) -> _roster.Roster | None:
    cur = paths.current_roster(fleet_name)
    return _roster.load(cur) if cur.exists() else None


def _probes(fleet: hosts.Fleet, args, kinds: tuple[str, ...] = ()) -> dict[str, doctor.Probe]:
    if args.probes_json:
        raw = json.loads(args.probes_json.read_text())
        return {k: doctor.Probe(**v) for k, v in raw.items()}
    out = {}
    for h in fleet.hosts:
        try:
            out[h.name] = doctor.check(make_session(h, h.sessions[0]), kinds=kinds)
        except NotImplementedInThisPass as exc:
            out[h.name] = doctor.Probe(host=h.name, reachable=False, error=str(exc))
    return out


def _emit(args, plan, extra: dict | None = None) -> None:
    if args.json:
        d = plan.to_dict()
        d.update(extra or {})
        print(json.dumps(d, indent=2))
    else:
        print(plan.render())


def _roster_table(ro: _roster.Roster, explain: bool = False) -> str:
    lines = [f"fleet {ro.fleet}  taken {ro.taken_at}  reason {ro.reason}  occupants {len(ro.occupants)}"]
    for h in ro.hosts:
        flag = "up" if h.reachable else f"DOWN ({h.error})"
        lines.append(f"  host {h.host}: {flag}  herdr {h.herdr.get('version')} {h.herdr.get('flavor')}" + ("  [cockpit]" if h.cockpit else ""))
    for o in ro.occupants:
        st = o.agent_status or "-"
        ids = o.live_ids.pane_id or "-"
        lines.append(f"  {o.slot_id[-8:]}  {o.role:<7} {o.kind or '-':<8} {st:<8} {ids:<7} {o.human_id}  {o.effective_cwd}")
        if explain:
            lines.append(f"            argv: {o.cmdline or '(shell)'}  tasking: {o.tasking or '-'}")
            if o.agent_config:
                c = o.agent_config
                d = c.get("disk") or {}
                lines.append(f"            config: permission={c.get('permission_mode')} model={c.get('model')} "
                             f"dir={c.get('config_dir')} version={d.get('version')} integration={d.get('integration')} "
                             f"plugins={len(d.get('plugins') or [])} hooks={sum((d.get('hooks') or {}).values())} "
                             f"mcp={len(d.get('mcp_servers') or [])} trusted={d.get('trusted')}"
                             + (f" secret-env={','.join(c['secret_env'])}" if c.get("secret_env") else ""))
    return "\n".join(lines)


# -- verbs ----------------------------------------------------------------

def cmd_roll(args) -> int:
    fleet = _fleet(args)
    ro, down = _roll(fleet, args, excerpts=args.excerpts)
    if args.json:
        print(json.dumps(collect.strip_excerpts(ro).to_dict(), indent=2))
    else:
        print(_roster_table(ro, explain=args.explain))
        for d in down:
            print(f"  not collected: {d}")
    return exitcodes.PARTIAL if down else exitcodes.OK


def _kinds(ro: _roster.Roster) -> tuple[str, ...]:
    return tuple(sorted({o.kind for o in ro.occupants if o.role == "agent" and o.kind}))


def cmd_snap(args) -> int:
    fleet = _fleet(args)
    ro, down = _roll(fleet, args, reason=args.reason, excerpts=args.excerpts)
    if not any(h.reachable for h in ro.hosts):
        print("no host reachable; roster not written (would be empty)", file=sys.stderr)
        return exitcodes.PARTIAL
    path, guard = _roster.write(ro, paths.rosters_dir(fleet.name), _guard_cfg(_read_config()), force=args.force)
    print(f"wrote {path}")
    if not guard.ok:
        print(f"current.json NOT retargeted: {guard.reason} (use --force to override)")
        return exitcodes.REFUSED
    print(f"current.json → {path.name} ({guard.reason})")
    return exitcodes.PARTIAL if down else exitcodes.OK


PRE_REASON = {"secure": "pre-secure", "relieve": "pre-relieve"}
POST_REASON = {"secure": "post-secure", "set": "post-set", "relieve": "post-set"}


def _run_plan(args, fleet, plan, roster: _roster.Roster | None = None) -> int:
    _emit(args, plan)
    if plan.refused:
        return exitcodes.REFUSED
    if not args.yes:
        return exitcodes.OK
    cfg = _read_config()
    # The plan's SNAP/GUARD markers are performed here, before the first mutating step.
    if roster is not None and plan.verb in PRE_REASON:
        roster.reason = PRE_REASON[plan.verb]
        path, guard = _roster.write(roster, paths.rosters_dir(fleet.name), _guard_cfg(cfg), force=getattr(args, "force", False))
        print(f"wrote {path} ({PRE_REASON[plan.verb]})")
        if not guard.ok:
            print(f"occupant guard refused: {guard.reason} (use --force)", file=sys.stderr)
            return exitcodes.REFUSED
    j = journal.Journal(paths.journal_file())
    res = _exec.run(plan, fleet, j, run_id=new_ulid(), force_server_stop=getattr(args, "force_server_stop", False))
    print(res.summary())
    if plan.verb in POST_REASON and res.code in (exitcodes.OK, exitcodes.PARTIAL):
        try:
            post, _down = _roll(fleet, args, reason=POST_REASON[plan.verb])
            if any(h.reachable for h in post.hosts):
                path, guard = _roster.write(post, paths.rosters_dir(fleet.name), _guard_cfg(cfg))
                print(f"wrote {path} ({POST_REASON[plan.verb]}; current.json {'retargeted' if guard.ok else 'kept: ' + guard.reason})")
        except NotImplementedInThisPass:
            print("post-run snap skipped: live collect not implemented yet; run `watchbill snap` after the MVP")
    return res.code


def cmd_secure(args) -> int:
    fleet = _fleet(args)
    ro, _down = _roll(fleet, args)
    cockpit_host, self_pane = _cockpit(fleet)
    opts = plan_secure.SecureOptions(mode=args.mode, targets=args.targets, host=args.host, force=args.force,
                                     include_local=args.include_local, force_server_stop=args.force_server_stop,
                                     approved=args.yes, self_pane=self_pane, cockpit_host=cockpit_host)
    return _run_plan(args, fleet, plan_secure.plan_secure(ro, fleet, opts), ro)


def cmd_set(args) -> int:
    fleet = _fleet(args)
    src = args.from_roster or paths.current_roster(fleet.name)
    if not src.exists():
        print(f"no roster at {src}; run `watchbill snap` first", file=sys.stderr)
        return exitcodes.USAGE
    ro = _roster.load(src)
    cockpit_host, self_pane = _cockpit(fleet)
    opts = plan_set.SetOptions(targets=args.targets, host=args.host, no_prompt=args.no_prompt,
                               include_local=args.include_local, approved=args.yes, self_pane=self_pane,
                               cockpit_host=cockpit_host, probes=_probes(fleet, args, _kinds(ro)))
    return _run_plan(args, fleet, plan_set.plan_set(ro, fleet, opts), ro)


def cmd_relieve(args) -> int:
    fleet = _fleet(args)
    cfg = _read_config()
    # Plan the window from a LIVE roll (merged with current.json for pins and
    # identity), never from the file alone. Who is safe to park is a question
    # about right now: rehearsal run 2 was refused because current.json had
    # been written the instant after a resume prompt, when the agent was
    # `working`; a minute later it was idle.
    ro, _down = _roll(fleet, args)
    cockpit_host, self_pane = _cockpit(fleet)
    j = journal.Journal(paths.journal_file())
    run_id = new_ulid()
    resume_done: set[str] = set()
    if args.resume:
        last = j.last_run_id("relieve")
        if last:
            run_id = last
            resume_done = j.hosts_marked(last, "set-complete")
    action_opts = {
        "kinds": [k for k in (args.kinds or "").split(",") if k], "plugin": args.plugin, "ref": args.ref,
        "startup_hooks": args.startup_hooks, "cmd": args.custom_cmd, "park": args.park, "session_stop": args.session_stop,
        "may_reboot": args.may_reboot,
        "no_prompt": args.no_prompt,
        "allow_partial_pacman": args.allow_partial_pacman or bool(cfg.get("relieve", {}).get("allow_partial_pacman", False)),
    }
    opts = plan_relieve.RelieveOptions(
        action=args.action, hosts=[args.host] if args.host else None, mode=args.mode, rolling=args.rolling,
        resume_done=resume_done, approved=args.yes, include_local=args.include_local,
        expected_version=args.expected_version, allow_reboot=args.allow_reboot, force=args.force,
        force_server_stop=args.force_server_stop, self_pane=self_pane, cockpit_host=cockpit_host,
        action_options=action_opts, probes=_probes(fleet, args, _kinds(ro)), run_id=run_id)
    return _run_plan(args, fleet, plan_relieve.plan_relieve(ro, fleet, opts), ro)


def cmd_status(args) -> int:
    fleet = _fleet(args)
    last = _load_current(fleet.name)
    live, down = _roll(fleet, args)
    if last is None:
        print("no current roster; run `watchbill snap`")
        print(_roster_table(live))
        return exitcodes.PARTIAL if down else exitcodes.OK
    last_ids = {o.slot_id: o for o in last.occupants}
    live_ids = {o.slot_id: o for o in live.occupants}
    added = [o for s, o in live_ids.items() if s not in last_ids]
    missing = [o for s, o in last_ids.items() if s not in live_ids]
    changed = [(last_ids[s], o) for s, o in live_ids.items() if s in last_ids and last_ids[s].agent_status != o.agent_status]
    print(f"last roster {last.taken_at} ({len(last.occupants)})  live now ({len(live.occupants)})")
    for o in added:
        print(f"  + {o.human_id} {o.role} {o.agent_status or ''}")
    for o in missing:
        print(f"  - {o.human_id} {o.role}")
    for a, b in changed:
        print(f"  ~ {b.human_id} {a.agent_status} → {b.agent_status}")
    for d in down:
        print(f"  down: {d}")
    return exitcodes.PARTIAL if down else exitcodes.OK


def cmd_doctor(args) -> int:
    fleet = _fleet(args)
    probes = _probes(fleet, args)
    if args.json:
        print(json.dumps({k: v.__dict__ for k, v in probes.items()}, indent=2))
    else:
        for p in probes.values():
            print(p.summary)
    return exitcodes.OK if all(p.reachable for p in probes.values()) else exitcodes.PARTIAL


COMMANDS = {"roll": cmd_roll, "snap": cmd_snap, "secure": cmd_secure, "set": cmd_set,
            "relieve": cmd_relieve, "overhaul": cmd_relieve, "status": cmd_status, "doctor": cmd_doctor}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return exitcodes.USAGE
    try:
        return COMMANDS[args.cmd](args)
    except exitcodes.WatchbillError as exc:
        print(f"{args.cmd}: {exc}", file=sys.stderr)
        return exc.code
    except NotImplementedInThisPass as exc:
        print(f"{args.cmd}: {exc}", file=sys.stderr)
        return exitcodes.TRANSPORT


if __name__ == "__main__":
    sys.exit(main())
