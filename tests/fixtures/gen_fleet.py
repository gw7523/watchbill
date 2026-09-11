"""Generate tests/fixtures/fleet.json — a synthetic two-host fleet in the
exact shape `herdr api snapshot` / `herdr pane process-info` / `herdr status
server --json` return on 0.8.2 (recorded 2026-09-11). Run with
`python tests/fixtures/gen_fleet.py` after changing anything here.

rig2 (cockpit, pacman):
  w1 personal-config : p1 claude idle, session A1
  w2 api             : p1 claude working, session A2 ; p2 watchexec watcher
  w3 api             : p1 codex idle, NO agent_session (unref)  → label dedupe "api#3"
  w4 Work            : p1 claude working = the Watchbill pane itself (self)
  w5 bridge          : p1 `herdr --remote ser6`  → bridge
ser6 (pacman):
  w1 sfl-site        : p1 claude blocked, session B1 ; p2 `npm run dev` server
  w2 personal-config : p1 grok idle, session B2   → collides with rig2 label
  w3 scratch         : p1 nvim editor ; p2 plain shell
"""
import json
from pathlib import Path

RECT = {"height": 40, "width": 100, "x": 0, "y": 0}
RECT_R = {"height": 40, "width": 100, "x": 101, "y": 0}


def pane(ws, tab, n, cwd, agent=None, status=None, session=None, title="", fg=None):
    d = {"pane_id": f"{ws}:p{n}", "tab_id": f"{ws}:t1", "workspace_id": ws, "cwd": cwd,
         "foreground_cwd": fg if fg is not None else cwd, "terminal_id": f"term_{ws}{n}",
         "terminal_title": title, "terminal_title_stripped": title, "revision": 1, "focused": False,
         "scroll": {"max_offset_from_bottom": 0, "offset_from_bottom": 0, "viewport_rows": 40}}
    if agent:
        d["agent"] = agent
        d["agent_status"] = status
        d["agent_session"] = {"agent": agent, "kind": "id", "source": f"herdr:{agent}", "value": session} if session else None
    return d


def proc(pid, argv, cwd):
    return {"foreground_process_group_id": pid, "shell_pid": pid - 1, "foreground_processes": [
        {"argv": argv, "cmdline": " ".join(argv), "cwd": cwd, "name": argv[0].rsplit("/", 1)[-1], "pid": pid}]}


def status(socket="/home/u/.config/herdr/herdr.sock"):
    return {"status": "running", "running": True, "version": "0.8.2", "protocol": 20,
            "capabilities": {"live_handoff": True, "detached_server_daemon": True},
            "compatible": True, "socket": socket, "session": None, "restart_needed": False}


def snapshot(workspaces, panes):
    tabs, layouts = [], []
    for ws in workspaces:
        wid = ws["workspace_id"]
        wpanes = [p for p in panes if p["workspace_id"] == wid]
        tabs.append({"tab_id": f"{wid}:t1", "workspace_id": wid, "label": "1", "number": 1,
                     "pane_count": len(wpanes), "agent_status": "idle", "focused": False})
        layouts.append({"tab_id": f"{wid}:t1", "workspace_id": wid, "area": RECT, "zoomed": False,
                        "focused_pane_id": wpanes[0]["pane_id"],
                        "panes": [{"pane_id": p["pane_id"], "focused": i == 0, "rect": RECT if i == 0 else RECT_R}
                                  for i, p in enumerate(wpanes)],
                        "splits": [] if len(wpanes) == 1 else [{"direction": "vertical", "ratio": 0.5}]})
        ws.update({"active_tab_id": f"{wid}:t1", "tab_count": 1, "pane_count": len(wpanes),
                   "agent_status": "idle", "focused": False})
    agents = [p for p in panes if p.get("agent")]
    return {"version": "0.8.2", "protocol": 20, "workspaces": workspaces, "tabs": tabs, "layouts": layouts,
            "panes": panes, "agents": [{k: v for k, v in a.items() if k != "scroll"} for a in agents],
            "focused_workspace_id": workspaces[0]["workspace_id"], "focused_tab_id": f"{workspaces[0]['workspace_id']}:t1",
            "focused_pane_id": panes[0]["pane_id"]}


H = "/home/u/Work"
rig2_panes = [
    pane("w1", 1, 1, f"{H}/personal-config", "claude", "idle", "a1a1a1a1-0000-4000-8000-000000000001", "btop pin"),
    pane("w2", 1, 1, f"{H}/api", "claude", "working", "a2a2a2a2-0000-4000-8000-000000000002", "API refactor"),
    pane("w2", 1, 2, f"{H}/api"),
    pane("w3", 1, 1, f"{H}/api-v2", "codex", "idle", None, "Codex tests"),
    pane("w4", 1, 1, f"{H}", "claude", "working", "c0c0c0c0-0000-4000-8000-0000000000c0", "Watchbill tool setup"),
    pane("w5", 1, 1, f"{H}", fg=f"{H}/sfl"),
]
rig2_ws = [{"workspace_id": "w1", "label": "personal-config", "number": 1}, {"workspace_id": "w2", "label": "api", "number": 2},
           {"workspace_id": "w3", "label": "api", "number": 3}, {"workspace_id": "w4", "label": "Work", "number": 4},
           {"workspace_id": "w5", "label": "bridge", "number": 5}]
rig2_pi = {
    "w1:p1": proc(1001, ["claude", "--dangerously-skip-permissions"], f"{H}/personal-config"),
    "w2:p1": proc(1002, ["claude"], f"{H}/api"),
    "w2:p2": proc(1003, ["watchexec", "-w", "src", "--", "pytest", "-q"], f"{H}/api"),
    "w3:p1": proc(1004, ["codex"], f"{H}/api-v2"),
    "w4:p1": proc(1005, ["claude", "--dangerously-skip-permissions"], H),
    "w5:p1": proc(1006, ["herdr", "--remote", "ser6.example", "--session", "default"], f"{H}/sfl"),
}
ser6_panes = [
    pane("w1", 1, 1, f"{H}/sfl-site", "claude", "blocked", "b1b1b1b1-0000-4000-8000-0000000000b1", "Deploy approval"),
    pane("w1", 1, 2, f"{H}/sfl-site"),
    pane("w2", 1, 1, f"{H}/personal-config", "grok", "idle", "b2b2b2b2-0000-4000-8000-0000000000b2", "ser6 overlay"),
    pane("w3", 1, 1, f"{H}/scratch"),
    pane("w3", 1, 2, f"{H}/scratch"),
]
ser6_ws = [{"workspace_id": "w1", "label": "sfl-site", "number": 1}, {"workspace_id": "w2", "label": "personal-config", "number": 2},
           {"workspace_id": "w3", "label": "scratch", "number": 3}]
ser6_pi = {
    "w1:p1": proc(2001, ["claude"], f"{H}/sfl-site"),
    "w1:p2": proc(2002, ["npm", "run", "dev"], f"{H}/sfl-site"),
    "w2:p1": proc(2003, ["grok"], f"{H}/personal-config"),
    "w3:p1": proc(2004, ["nvim", "notes.md"], f"{H}/scratch"),
    "w3:p2": {"foreground_process_group_id": 2005, "shell_pid": 2005, "foreground_processes": []},
}

fleet = {
    "rig2": {"cockpit": True, "herdr_path": "/usr/bin/herdr", "pacman_owned": True,
             "sessions": {"default": {"status": status(), "snapshot": snapshot(rig2_ws, rig2_panes), "process_info": rig2_pi}}},
    "ser6": {"cockpit": False, "herdr_path": "/usr/bin/herdr", "pacman_owned": True,
             "sessions": {"default": {"status": status(), "snapshot": snapshot(ser6_ws, ser6_panes), "process_info": ser6_pi}}},
}
probes = {
    "rig2": {"host": "rig2", "reachable": True, "version": "0.8.2", "protocol": 20, "compatible": True, "running": True,
             "flavor": "pacman", "herdr_path": "/usr/bin/herdr", "live_handoff_flag": True, "handoff_supported": False,
             "agent_versions": {"claude": "2.1.267", "codex": "0.153.4"}, "agent_flavors": {"claude": "mise", "codex": "mise"}},
    "ser6": {"host": "ser6", "reachable": True, "version": "0.8.2", "protocol": 20, "compatible": True, "running": True,
             "flavor": "pacman", "herdr_path": "/usr/bin/herdr", "live_handoff_flag": True, "handoff_supported": False,
             "agent_versions": {"claude": "2.1.267", "grok": "1.0.25"}, "agent_flavors": {"claude": "mise", "grok": "npm"}},
    "vps": {"host": "vps", "reachable": True, "version": "0.8.2", "protocol": 20, "compatible": True, "running": True,
            "flavor": "official", "herdr_path": "/home/u/.local/bin/herdr", "live_handoff_flag": True, "handoff_supported": True,
            "agent_versions": {"claude": "2.1.267"}, "agent_flavors": {"claude": "unknown"}},
}
here = Path(__file__).parent
(here / "fleet.json").write_text(json.dumps(fleet, indent=1) + "\n")
(here / "probes.json").write_text(json.dumps(probes, indent=1) + "\n")
print("wrote fleet.json probes.json")
