"""tmux and cmux backends, capability gating, and agent-status heuristics."""
from pathlib import Path

import pytest

from watchbill import actions, collect, detect, hosts
from watchbill import mux as M
from watchbill.exitcodes import RefusedPlan
from watchbill.mux.base import MuxSnapshot
from watchbill.plan import check_verbs_allowed
from watchbill.plan_relieve import RelieveOptions, plan_relieve
from watchbill.plan_secure import SecureOptions, plan_secure
from watchbill.plan_set import SetOptions, plan_set
from watchbill.slots import SlotStore

FIX = Path(__file__).parent / "fixtures" / "tmux-3.7c.txt"
NOW = 1789159586 + 120   # two minutes after the capture → everything "idle" unless the screen says otherwise

TMUX_HOSTS = """
[fleet]
name = "mixed"
[[host]]
name = "rig2"
cockpit = true
transport = "local"
[[host]]
name = "mac"
target = "mac.example"
mux = "tmux"
[[host]]
name = "air"
target = "air.example"
mux = "cmux"
"""


def tmux_outputs():
    text = "\n".join(ln for ln in FIX.read_text().splitlines() if not ln.startswith("#"))
    return text.split("---\n")


def ps_for(pane_id):
    return {"%0": "3345069 3345068 Ss  -bash\n3345100 3345069 S+  claude --dangerously-skip-permissions\n/home/u/Work/api\n",
            "%1": "3345072 3345068 Ss  -bash\n3345101 3345072 S+  watchexec -w src -- pytest -q\n/home/u/Work/api\n",
            "%2": "3345075 3345068 Ss  -bash\n3345102 3345075 S+  claude\n/home/u/Work/api\n",
            "%3": "3345078 3345068 Ss+ bash\n/tmp\n"}[pane_id]


@pytest.fixture
def mixed_fleet():
    return hosts.parse(TMUX_HOSTS, hostname="rig2")


@pytest.fixture
def tmux_facts():
    be = M.TmuxBackend()
    snap = be.parse_snapshot(tmux_outputs(), now=NOW)
    sf = collect.SessionFacts("default", status={"version": "3.7c", "running": True}, snapshot=snap, running=True, version="3.7c")
    for p in snap.panes:
        sf.process_info[p.pane_id] = be.parse_process_info(p, ps_for(p.pane_id))
        sf.excerpts[p.pane_id] = "some output\n$ " if p.pane_id != "%2" else "Do you want to proceed?\n❯ 1. Yes\n  2. No\n"
    return [collect.HostFacts(host="mac", mux="tmux", herdr_path="/opt/homebrew/bin/tmux", sessions=[sf])]


@pytest.fixture
def tmux_roster(tmux_facts, allowlist):
    return collect.build_roster("mixed", tmux_facts, slots=SlotStore(), allowlist=allowlist,
                                cockpit={"host": "rig2", "pane_id": "w4:p1"}, now="2026-09-11T12:00:00+00:00")


# -- parsing ---------------------------------------------------------------

def test_tmux_parse_snapshot_from_captured_output():
    snap = M.TmuxBackend().parse_snapshot(tmux_outputs(), now=NOW)
    assert [w.label for w in snap.workspaces] == ["alpha", "beta"]
    edit = snap.tab("@0")
    assert edit.label == "edit" and edit.layout.startswith("8205,80x24,0,0{")
    p1 = next(p for p in snap.panes if p.pane_id == "%1")
    assert (p1.workspace_id, p1.tab_id, p1.index, p1.cwd, p1.tty) == ("$0", "@0", 2, "/home/u/Work/api", "/dev/pts/9")
    assert p1.rect == {"x": 41, "y": 0, "width": 39, "height": 24} and p1.current_command == "watchexec"
    assert p1.activity_age_s == 120


def test_tmux_process_info_foreground_only():
    be = M.TmuxBackend()
    pane = M.MuxPane(pane_id="%0", workspace_id="$0", tab_id="@0", pid=3345069, tty="/dev/pts/6", cwd="/x")
    info = be.parse_process_info(pane, ps_for("%0"))
    assert [p["argv"] for p in info["foreground_processes"]] == [["claude", "--dangerously-skip-permissions"]]
    assert info["foreground_processes"][0]["cwd"] == "/home/u/Work/api"
    assert be.process_info_argv(pane)[:2] == ["sh", "-c"] and "ps -t pts/6" in be.process_info_argv(pane)[2]


def test_tmux_created_ids_and_layout_argv():
    be = M.TmuxBackend()
    assert be.created_ids("$3|%9\n") == {"workspace_id": "$3", "pane_id": "%9"}
    assert be.created_ids("@4|%10\n") == {"tab_id": "@4", "pane_id": "%10"}
    assert be.workspace_create("api", "/w")[:6] == ["new-session", "-d", "-s", "api", "-c", "/w"]
    assert be.pane_split("%0", "right", "/w")[:5] == ["split-window", "-d", "-t", "%0", "-h"]
    assert be.layout_apply("api:edit", "8205,80x24,0,0{…}") == ["select-layout", "-t", "api:edit", "8205,80x24,0,0{…}"]
    assert be.send_text("%0", "/exit") == ["send-keys", "-t", "%0", "-l", "/exit"]
    assert not be.is_mutating(["list-panes", "-a"]) and be.is_mutating(["send-keys", "-t", "%0", "Enter"])


# -- classification + heuristics --------------------------------------------

def test_tmux_roster_roles_status_and_continue_resume(tmux_roster):
    r = tmux_roster
    by = {o.human_id: o for o in r.occupants}
    a = by["mac/default/alpha/edit/p1"]
    assert (a.role, a.kind, a.mux) == ("agent", "claude", "tmux")
    assert a.agent_status == "idle" and a.agent_session is None
    # two claude agents share /home/u/Work/api → continue-form is ambiguous → no auto resume
    assert a.resume_argv is None and "ambiguous" in (a.tasking or "")
    b = by["mac/default/alpha/logs/p1"]
    assert b.agent_status == "blocked"              # screen shows an approval prompt
    w = by["mac/default/alpha/edit/p2"]
    assert w.role == "watcher" and w.allow_relaunch and w.live_ids.pane_id == "%1"
    assert by["mac/default/beta/tmp/p1"].role == "shell"
    shape = r.shape_for("mac", "default")
    assert shape.mux == "tmux" and shape.workspaces[0]["tabs"][0]["layout"].startswith("8205,")


def test_tmux_continue_form_when_cwd_is_unique(tmux_facts, allowlist):
    sf = tmux_facts[0].sessions[0]
    p2 = next(p for p in sf.snapshot.panes if p.pane_id == "%2")
    p2.cwd = "/home/u/Work/other"
    sf.process_info["%2"] = M.TmuxBackend().parse_process_info(p2, "3345075 3345068 Ss  -bash\n3345102 3345075 S+  claude\n/home/u/Work/other\n")
    r = collect.build_roster("mixed", tmux_facts, slots=SlotStore(), allowlist=allowlist)
    a = r.by_human("mac/default/alpha/edit/p1")
    assert a.resume_argv == ["claude", "--continue"]


def test_detect_heuristics():
    assert detect.agent_status("claude", activity_age_s=5, screen="working…") == "working"
    assert detect.agent_status("claude", activity_age_s=60, screen="$ ") == "idle"
    assert detect.agent_status("claude", activity_age_s=60, screen="Do you want to run this?\n❯ 1. Yes") == "blocked"
    assert detect.agent_status("codex", activity_age_s=None, screen=None) == "unknown"
    assert detect.status_is_parkable("idle") and not detect.status_is_parkable("unknown")


# -- planners on tmux ---------------------------------------------------------

def opts(**kw):
    base = dict(cockpit_host="rig2", self_pane="w4:p1")
    base.update(kw)
    return SecureOptions(**base)


def test_tmux_secure_park_uses_send_keys_and_refuses_blocked(tmux_roster, mixed_fleet):
    p = plan_secure(tmux_roster, mixed_fleet, opts(targets=["mac/default/alpha/edit/p1", "mac/default/alpha/logs/p1"]))
    assert any("blocked" in r.reason for r in p.refusals)          # logs/p1 never typed into
    raws = [s.raw for s in p.steps if s.mux == "tmux"]
    assert ("send-keys", "-t", "%0", "-l", "/exit") in raws and ("send-keys", "-t", "%0", "Enter") in raws
    wait = next(s for s in p.steps if s.kind.value == "wait")
    assert wait.via == "shell" and "pane_current_command" in wait.raw[2] and "tmux -L default" in wait.raw[2]
    ssh = next(s for s in p.steps if s.raw[:1] == ("send-keys",))
    assert ssh.argv[0] == "ssh" and ssh.argv[-1].startswith("tmux -L default send-keys")
    assert not [s for s in p.scheduled() if s.mutating]


def test_tmux_unknown_status_needs_force(tmux_roster, mixed_fleet):
    o = tmux_roster.by_human("mac/default/alpha/edit/p1")
    o.agent_status = "unknown"
    p = plan_secure(tmux_roster, mixed_fleet, opts(targets=[o.human_id]))
    assert p.refused and p.refusals[0].override == "--force" and "cannot prove" in p.refusals[0].reason


def test_tmux_dismiss_is_kill_server_and_guarded(tmux_roster, mixed_fleet):
    p = plan_secure(tmux_roster, mixed_fleet, opts(mode="dismiss", targets=["beta"], host="mac"))
    stop = next(s for s in p.steps if s.raw == ("kill-server",))
    assert stop.mutating and check_verbs_allowed(p.steps) and not check_verbs_allowed(p.steps, force_server_stop=True)


def test_tmux_set_no_viewport_layout_reapplied(tmux_roster, mixed_fleet, probes):
    pr = dict(probes)
    pr["mac"] = probes["vps"].__class__(**{**probes["vps"].__dict__, "host": "mac", "running": False, "flavor": "brew", "handoff_supported": False})
    p = plan_set(tmux_roster, mixed_fleet, SetOptions(targets=["alpha"], host="mac", cockpit_host="rig2", self_pane="w4:p1", probes=pr))
    ids = [s.id for s in p.steps]
    assert not any("att" in i for i in ids)                                  # tmux needs no #2064 viewport
    start = next(s for s in p.steps if s.id == "set1.start")
    assert start.raw[:4] == ("new-session", "-d", "-s", "alpha") and start.creates == "boot:mac/default"
    assert not any(s.raw[:3] == ("new-session", "-d", "-s") and s.raw[3] == "alpha" and s.id != "set1.start" for s in p.steps)
    assert any(s.raw[:2] == ("new-window", "-d") and "logs" in s.raw for s in p.steps)
    assert any(s.raw[:3] == ("split-window", "-d", "-t") for s in p.steps)
    lay = next(s for s in p.steps if s.raw[:1] == ("select-layout",))
    assert lay.raw[2] == "alpha:edit" and lay.raw[3].startswith("8205,")
    starts = [s for s in p.steps if s.raw[:4] == ("send-keys", "-t", "{pane:" + tmux_roster.by_human("mac/default/alpha/edit/p1").slot_id + "}", "-l")]
    assert starts and starts[0].raw[4] == "claude"                            # ambiguous cwd → fresh start
    assert not any("--current" in s.argv for s in p.steps)


def test_tmux_watcher_relaunch_is_respawn_pane(tmux_roster, mixed_fleet, probes):
    p = plan_set(tmux_roster, mixed_fleet, SetOptions(targets=["mac/default/alpha/edit/p2"], cockpit_host="rig2", self_pane="w4:p1", probes={"mac": probes["vps"]}, live=tmux_roster))
    run = next(s for s in p.steps if s.raw[:1] == ("respawn-pane",))
    assert run.raw[1:5] == ("-k", "-t", "%1", "-c") and run.raw[-6:] == ("watchexec", "-w", "src", "--", "pytest", "-q")


def test_tmux_relieve_live_refused_reload_config_live(tmux_roster, mixed_fleet, probes):
    pr = {"mac": probes["vps"].__class__(**{**probes["vps"].__dict__, "host": "mac", "flavor": "brew", "handoff_supported": True})}
    with pytest.raises(RefusedPlan, match="no live handoff"):
        plan_relieve(tmux_roster, mixed_fleet, RelieveOptions(action="upgrade-mux", hosts=["mac"], mode="live", cockpit_host="rig2", probes=pr))
    p = plan_relieve(tmux_roster, mixed_fleet, RelieveOptions(action="reload-config", hosts=["mac"], cockpit_host="rig2", probes=pr))
    assert not p.refused
    raws = [s.raw for s in p.steps]
    assert ("source-file", "~/.tmux.conf") in raws and ("kill-server",) not in raws
    assert not any(s.raw[:1] == ("send-keys",) for s in p.steps)             # nothing parked
    cold = plan_relieve(tmux_roster, mixed_fleet, RelieveOptions(action="upgrade-mux", hosts=["mac"], cockpit_host="rig2", probes=pr, force=True))
    assert cold.refused                                                      # blocked claude in logs/p1
    tmux_roster.by_human("mac/default/alpha/logs/p1").agent_status = "idle"
    cold = plan_relieve(tmux_roster, mixed_fleet, RelieveOptions(action="upgrade-mux", hosts=["mac"], cockpit_host="rig2", probes=pr))
    ids = [s.id for s in cold.steps]
    assert ids.index("mac.stop1") < ids.index("mac.act1") < ids.index("mac.set1.start")
    assert next(s for s in cold.steps if s.id == "mac.act1").raw == ("brew", "upgrade", "tmux")
    assert not any("keep" in i for i in ids)                                 # no session.json on tmux


def test_tmux_install_plugin_is_live_tpm(mixed_fleet, probes):
    a = actions.get("install-plugin", actions.ActionContext(options={"plugin": "tmux-plugins/tmux-resurrect"}, probes=probes))
    cmds = a.commands(mixed_fleet.host("mac"), probes["vps"])
    assert cmds[-1].argv == ("tmux", "source-file", "~/.tmux.conf") and cmds[-1].via == "mux"
    assert not a.blast_radius().needs_session_stop
    with pytest.raises(actions.ActionUnavailable):
        a.commands(mixed_fleet.host("air"), probes["vps"])


# -- cmux (docs-only) --------------------------------------------------------

def test_cmux_backend_fails_closed_and_manual_steps(mixed_fleet, probes):
    be = M.CmuxBackend()
    assert be.caps.docs_only and be.process_info_argv(M.MuxPane("s1", "w1", "p1")) is None and be.excerpt_argv("s1", 10) is None
    assert be.session_stop("app") is None and be.reload_config() is None
    snap = be.parse_snapshot(['[{"uuid":"W1","title":"api"}]', '[{"uuid":"P1","workspace":"W1"}]', '[{"uuid":"S1","panel":"P1","workspace":"W1","cwd":"/w"}]'])
    assert snap.panes[0].pane_id == "S1" and snap.tabs[0].label == "panel1"
    sf = collect.SessionFacts("app", snapshot=snap, running=True, bindings={"S1": {"command": "claude --resume abc"}})
    r = collect.build_roster("mixed", [collect.HostFacts(host="air", mux="cmux", sessions=[sf])], slots=SlotStore())
    o = r.occupants[0]
    assert (o.role, o.kind, o.resume_argv, o.agent_status) == ("agent", "claude", ["claude", "--resume", "abc"], "unknown")
    # secure: unknown status → --force; dismiss → refused (no server); relieve restart → MANUAL steps
    p = plan_secure(r, mixed_fleet, opts(targets=[o.human_id]))
    assert p.refused and p.refusals[0].override == "--force"
    p = plan_secure(r, mixed_fleet, opts(mode="dismiss", targets=[o.human_id], force=True))
    assert any("no server" in x.reason for x in p.refusals)
    pr = {"air": probes["vps"].__class__(**{**probes["vps"].__dict__, "host": "air", "flavor": "brew", "handoff_supported": False})}
    w = plan_relieve(r, mixed_fleet, RelieveOptions(action="restart-harness", hosts=["air"], cockpit_host="rig2", probes=pr, force=True))
    kinds = [s.kind.value for s in w.steps]
    # three hand-offs to the operator: confirm the agent exited, quit cmux, relaunch cmux
    assert kinds.count("manual") == 3 and any(s.raw == ("restore-session",) for s in w.steps)
    assert all(s.unverified for s in w.steps if s.mux == "cmux" and s.via == "mux" and s.mutating)
    with pytest.raises(RefusedPlan):
        plan_relieve(r, mixed_fleet, RelieveOptions(action="restart-harness", hosts=["air"], mode="live", cockpit_host="rig2", probes=pr))
    rc = plan_relieve(r, mixed_fleet, RelieveOptions(action="reload-config", hosts=["air"], cockpit_host="rig2", probes=pr))
    assert rc.refused and "no config reload" in rc.refusals[0].reason


def test_capability_matrix_and_hosts_toml():
    caps = M.CAPS
    assert caps["herdr"].needs_viewport and not caps["tmux"].needs_viewport
    assert caps["tmux"].layout_reapply and caps["tmux"].live_reload and caps["tmux"].live_handoff == "never"
    assert caps["cmux"].docs_only and not caps["cmux"].has_server
    assert set(actions.per_mux_matrix()) == set(actions.REGISTRY) - {"upgrade-herdr"} | {"upgrade-mux"} - {"upgrade-herdr"}
    with pytest.raises(ValueError):
        hosts.parse('[[host]]\nname = "x"\ntarget = "x"\nmux = "screen"\n')
    with pytest.raises(ValueError):
        hosts.parse('[[host]]\nname = "x"\ntarget = "x"\nmux = "tmux"\ntransport = "herdr_remote"\n')


def test_manual_step_exec_needs_confirmation(mixed_fleet, tmp_path):
    from watchbill import exec as X
    from watchbill.journal import Journal
    from watchbill.plan import Plan, Step, StepKind
    plan = Plan(verb="relieve", fleet="mixed", approved=True)
    plan.add(Step(id="m", kind=StepKind.MANUAL, host="air", mux="cmux", description="MANUAL: relaunch cmux", mutating=True))
    j = Journal(tmp_path / "j.jsonl")
    assert X.Executor(mixed_fleet, j, run_id="r", confirm=lambda t: False).run(plan).failed
    assert not X.Executor(mixed_fleet, j, run_id="r", confirm=lambda t: True).run(plan).failed
