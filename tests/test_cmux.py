"""cmux backend against outputs captured from cmux 0.64.22 on the Mac mini
(tests/fixtures/cmux-0.64/, 2026-09-14): tree, workspace list, top, sessions,
surface resume show. What the live probes established is pinned here so the
composition (collect → plan → exec argv) cannot drift from the app."""
import json
import shlex
from pathlib import Path

import pytest

from watchbill import actions, collect, doctor, hosts
from watchbill import mux as M
from watchbill.exitcodes import RefusedPlan
from watchbill.mux.base import POLL, MuxPane
from watchbill.plan import check_verbs_allowed
from watchbill.plan_relieve import RelieveOptions, plan_relieve
from watchbill.plan_secure import SecureOptions, plan_secure
from watchbill.slots import SlotStore

FX = Path(__file__).parent / "fixtures" / "cmux-0.64"
SF = "34842749-2606-4025-9F6F-4C3DEFD06071"          # the surface running the throwaway Claude
WS = "CA999742-C6B2-4704-9446-4C6F21A1C8D5"
SID = "816f2237-f722-4976-939a-f0d1d1e9c6dd"
PS = ("tty=ttys008\n"
      "78120 78112 Ss   /usr/bin/login -flp user /bin/bash --noprofile --norc -c exec -l /opt/homebrew/bin/bash\n"
      "78127 78120 S    -/opt/homebrew/bin/bash\n"
      f"78320 78127 S+   /Users/user/.local/bin/claude --session-id {SID} "
      '--settings {"preferredNotifChannel":"notifications_disabled"} --model haiku\n'
      "/private/tmp/wb-cmux\n")

HOSTS = """
[fleet]
name = "mac"
[[host]]
name = "rig2"
cockpit = true
transport = "local"
[[host]]
name = "mini"
target = "user@mini.example"
mux = "cmux"
sessions = ["app"]
"""


def outputs(lifecycle: str | None = None):
    outs = [(FX / f).read_text() for f in ("tree.json", "workspace-list.json", "top.json", "sessions.json")]
    if lifecycle:
        d = json.loads(outs[3])
        for s in d["sessions"]:
            if s.get("session_id"):
                s["agent_lifecycle"] = lifecycle
        outs[3] = json.dumps(d)
    return outs + ["cmux 0.64.22 (102) [ddd4a01bc]\n"]


def facts(lifecycle: str | None = None):
    be = M.CmuxBackend()
    snap = be.parse_snapshot(outputs(lifecycle))
    pane = snap.pane(SF) if hasattr(snap, "pane") else next(p for p in snap.panes if p.pane_id == SF)
    pinfo = {SF: be.parse_process_info(pane, PS)}
    sf = collect.SessionFacts("app", status={"running": True, "version": "0.64.22"}, snapshot=snap, running=True,
                              version="0.64.22", process_info=pinfo,
                              bindings={SF: json.loads((FX / "resume-show.json").read_text())})
    return sf


def roster(lifecycle: str | None = None):
    return collect.build_roster("mac", [collect.HostFacts(host="mini", mux="cmux", sessions=[facts(lifecycle)])], slots=SlotStore())


@pytest.fixture
def fleet():
    return hosts.parse(HOSTS, hostname="rig2")


def probes_for(fleet):
    return {"mini": doctor.Probe(host="mini", reachable=True, running=True, flavor="brew", mux="cmux", version="0.64.22")}


def test_snapshot_reads_tree_top_and_hook_sessions():
    be = M.CmuxBackend()
    snap = be.parse_snapshot(outputs())
    assert snap.version == "0.64.22"
    assert [w.label for w in snap.workspaces] == ["wbprobe", "~"] and snap.workspaces[0].workspace_id == WS
    assert len(snap.panes) == 3 and all(len(p.pane_id) == 36 for p in snap.panes)   # uuids, never refs
    p = next(p for p in snap.panes if p.pane_id == SF)
    assert p.workspace_id == WS and p.cwd == "/tmp" and p.index == 1 and p.pid == 78127   # oldest pid = the shell
    assert p.agent == "claude" and p.agent_status == "unknown" and p.agent_session["value"] == SID
    assert p.agent_session["restorable"] is True
    other = [q for q in snap.panes if q.pane_id != SF]
    assert all(q.agent is None and q.agent_status is None for q in other)
    idle = M.CmuxBackend().parse_snapshot(outputs("idle"))
    assert next(p for p in idle.panes if p.pane_id == SF).agent_status == "idle"
    assert M.CmuxBackend().parse_snapshot(outputs("needsInput")).panes[0].agent_status in ("blocked", None)


def test_needs_input_is_blocked_only_when_the_screen_shows_a_dialog():
    """Claude's Notification hook sets `needsInput` for an idle notice too
    (live: idle → needsInput a minute after a reply), which refused a park."""
    quiet = "⏺ TANGERINE\n\n✻ Worked for 2s · done 10:08 AM\n\n❯ \n  ⏸ manual mode on · ? for shortcuts\n"
    dialog = quiet + "Do you want to proceed?\n ❯ 1. Yes\n   2. No\n"
    for screen, want in ((quiet, "idle"), (dialog, "blocked"), (None, "blocked")):
        sf = facts("needsInput")
        if screen is not None:
            sf.excerpts[SF] = screen
        r = collect.build_roster("mac", [collect.HostFacts(host="mini", mux="cmux", sessions=[sf])], slots=SlotStore())
        assert next(o for o in r.occupants if o.kind == "claude").agent_status == want, screen


def test_process_info_uses_surface_env_for_the_tty_and_skips_login_and_wrapper_rows():
    be = M.CmuxBackend()
    argv = be.process_info_argv(MuxPane(SF, WS, "p"))
    assert argv[:2] == ["sh", "-c"] and f"CMUX_SURFACE_ID={SF}" in argv[2] and "ps -t" in argv[2] and "lsof" in argv[2]
    pane = MuxPane(SF, WS, "p", pid=78127, cwd="/tmp")
    info = be.parse_process_info(pane, PS)
    assert pane.tty == "ttys008"
    fg = info["foreground_processes"]
    assert [p["name"] for p in fg] == ["claude"] and fg[0]["cwd"] == "/private/tmp/wb-cmux"
    assert "--session-id" in fg[0]["argv"] and info["shell_pid"] == 78127
    assert be.process_info_argv(MuxPane("b", WS, "p", extra={"non_terminal": True})) is None   # browser surface


def test_roster_carries_the_hook_session_id_and_flags_but_not_the_session_selector():
    r = roster("idle")
    o = next(o for o in r.occupants if o.live_ids.pane_id == SF)
    assert (o.role, o.kind, o.agent_status) == ("agent", "claude", "idle")
    assert o.agent_session["value"] == SID and o.agent_session["auto_resume"] is True
    assert o.resume_argv[0] == "claude" and "--resume" in o.resume_argv and SID in o.resume_argv
    assert "--session-id" not in o.resume_argv and "--model" in o.resume_argv and "haiku" in o.resume_argv
    assert o.resume_argv == ["claude", "--resume", SID, "--model", "haiku"]   # cmux's own prepared_arguments, no --settings blob
    assert o.effective_cwd == "/private/tmp/wb-cmux"
    shells = [o for o in r.occupants if o.role == "shell"]
    assert len(shells) == 2


def test_secure_parks_through_send_and_a_shell_poll(fleet):
    r = roster("idle")
    o = next(o for o in r.occupants if o.kind == "claude")
    p = plan_secure(r, fleet, SecureOptions(targets=[o.human_id], cockpit_host="rig2"))
    raws = [s.raw for s in p.steps if s.slot_id == o.slot_id]
    assert ("send-key", "--surface", SF, "ctrl-u") in raws and ("send", "--surface", SF, "/exit") in raws
    wait = next(s for s in p.steps if s.slot_id == o.slot_id and s.kind.value == "wait")
    assert wait.via == "shell" and wait.mutating and "send-key --surface" in " ".join(wait.raw)   # the Enter nudge
    assert "ps -axE" in " ".join(wait.raw)
    # cmux lifecycle `unknown` (fresh relaunch, no turn yet) is not parkable without --force
    r2 = roster()
    p2 = plan_secure(r2, fleet, SecureOptions(targets=[o.human_id], cockpit_host="rig2"))
    assert p2.refused and p2.refusals[0].override == "--force"


def test_restart_harness_quits_relaunches_and_waits_for_the_native_resume(fleet):
    r = roster("idle")
    o = next(o for o in r.occupants if o.kind == "claude")
    w = plan_relieve(r, fleet, RelieveOptions(action="restart-harness", hosts=["mini"], cockpit_host="rig2", probes=probes_for(fleet)))
    assert not w.refused, w.refusals
    ids = [s.id for s in w.steps]
    stop = next(s for s in w.steps if s.id == "mini.stop1")
    assert stop.via == "shell" and stop.planned_stop and 'tell application "cmux" to quit' in " ".join(stop.raw)
    start = next(s for s in w.steps if s.id.endswith("set1.start"))
    assert start.via == "shell" and "open -a cmux" in " ".join(start.raw)
    up = next(s for s in w.steps if s.id.endswith("set1.up"))
    assert "ping" in " ".join(up.raw) and "surface_ref" in " ".join(up.raw)
    res = next(s for s in w.steps if s.slot_id == o.slot_id and s.id.endswith(".start"))
    assert res.kind.value == "wait" and res.mutating and "fallback" not in res.raw   # resolved to sh
    body = " ".join(res.raw)
    assert f"--resume {SID}" in body and "send --surface" in body and "[ \"$ag\" = 1 ]" in body
    assert ids.index("mini.stop1") < ids.index(start.id) < ids.index(res.id) < ids.index("mini.verify1")
    assert not check_verbs_allowed(w.steps)
    assert not any(s.kind.value == "manual" for s in w.steps)
    # seat env is not exported into a surface cmux is about to resume itself
    assert not any(s.id.endswith(".env") for s in w.steps)


def test_unplanned_cmux_quit_is_a_violation():
    from watchbill.plan import Step, StepKind
    raw = tuple(M.CmuxBackend().session_stop_shell("app"))
    s = Step(id="x", kind=StepKind.SHELL, host="mini", mux="cmux", via="shell", raw=raw, argv=raw, description="", mutating=True)
    assert check_verbs_allowed([s]) and not check_verbs_allowed([s], force_server_stop=True)


def test_reload_config_is_live_on_cmux(fleet):
    r = roster("idle")
    rc = plan_relieve(r, fleet, RelieveOptions(action="reload-config", hosts=["mini"], cockpit_host="rig2", probes=probes_for(fleet)))
    assert not rc.refused and any(s.raw == ("reload-config",) for s in rc.steps)
    with pytest.raises(RefusedPlan):
        plan_relieve(r, fleet, RelieveOptions(action="restart-harness", hosts=["mini"], mode="live", cockpit_host="rig2", probes=probes_for(fleet)))
    assert actions.per_mux_matrix()["reload-config"]["cmux"].startswith("live")


def test_mutation_classification_ids_and_polls():
    be = M.CmuxBackend(socket="/x/cmux.sock", idle_after_s=6)
    assert be.cli_prefix("app") == ["cmux", "--socket", "/x/cmux.sock"]
    for ro in (["--json", "--id-format", "both", "tree", "--all"], ["--json", "workspace", "list"], ["ping"],
               ["--json", "surface", "resume", "show", "--surface", SF], ["read-screen", "--surface", SF, "--lines", "8"]):
        assert not be.is_mutating(ro), ro
    for mut in (["send", "--surface", SF, "x"], ["--json", "workspace", "create", "--name", "a"], ["close-workspace", "--workspace", WS],
                ["reload-config"], ["surface", "resume", "set", "--shell", "x"]):
        assert be.is_mutating(mut), mut
    assert be.created_ids('{"surface_id":"S","pane_id":"P","workspace_id":"W"}') == {"pane_id": "S", "tab_id": "P", "workspace_id": "W"}
    assert be.created_ids("OK workspace:3\n") == {"workspace_id": "workspace:3"}
    assert be.workspace_create("api", "/w", env={"A": "1"})[-4:] == ["--focus", "false", "--env", "A=1"]
    assert be.pane_split(SF, "down", "/w")[:5] == ["--json", "--id-format", "uuids", "new-split", "down"]
    assert be.clear_input(SF)[-1] == "ctrl-u" and be.interrupt(SF)[-1] == "ctrl-c" and be.send_enter(SF)[-1] == "enter"
    shell = be.resolve_poll("app", be.agent_wait_exit(SF, "grok", 5000))
    assert shell[:2] == ["sh", "-c"] and "grok" in shell[2] and "cmux --socket /x/cmux.sock send-key" in shell[2]
    idle = be.resolve_poll("app", be.agent_wait_idle(SF, 5000))
    assert "sessions list --surface" in idle[2] and "read-screen" in idle[2] and '-ge 6' in idle[2] and "Do you want to" in idle[2]
    # planners hand over `{pane:<slot>}` placeholders; braces must survive every mode (KeyError, live 2026-09-14)
    for argv in (be.agent_wait_idle("{pane:01ABC}", 5000), be.agent_wait_exit("{pane:01ABC}", "claude", 5000),
                 be.agent_native_resume_wait("{pane:01ABC}", "claude", ["claude", "--resume", "x"], 9000)):
        assert "{pane:01ABC}" in be.resolve_poll("app", argv)[2]
    agent = be.resolve_poll("app", be.agent_native_resume_wait(SF, "claude", ["claude", "--resume", "x"], 30000))
    assert "claude --resume x" in agent[2] and '[ "$i" = 10 ]' in agent[2]
    assert be.resolve_poll("app", ["send", "x"]) == ["send", "x"]
    with pytest.raises(ValueError):
        be.resolve_poll("app", [POLL, SF, "bogus", "1000", ""])
    assert shlex.split(be.session_stop_shell("app")[2])   # well-formed shell


def test_hosts_default_start_and_doctor_summary():
    f = hosts.parse(HOSTS, hostname="rig2")
    assert f.host("mini").start == "open -a cmux" and f.host("rig2").start == hosts.DEFAULT_START
    p = doctor.Probe(host="mini", reachable=True, running=True, flavor="brew", mux="cmux", version="0.64.22",
                     warnings=["app.confirmQuit is not \"never\""])
    assert "cmux 0.64.22" in p.summary and "proto" not in p.summary and "warning: app.confirmQuit" in p.summary
