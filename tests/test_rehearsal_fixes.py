"""Pins for the five bugs found while preparing the park/stop/resume rehearsal
(lane 3). Each was found by probing a live herdr 0.8.2 or by reading the
resume path, before anything was run against a real agent."""
from watchbill import exec as X
from watchbill import hosts, mux, resume
from watchbill.plan import StepKind
from watchbill.plan_set import SetOptions, plan_set


# 1. `herdr --session S server` stays in the foreground --------------------

def test_default_herdr_start_detaches():
    h = hosts.Host(name="x", target="x")
    cmd = h.start_cmd("wb")
    # both branches detach with every descriptor redirected: setsid where it exists, nohup elsewhere
    assert "setsid -f herdr --session wb server </dev/null >/dev/null 2>&1" in cmd
    assert "(nohup herdr --session wb server </dev/null >/dev/null 2>&1 &)" in cmd


# 2. status exits 0 for a stopped session ------------------------------------

def test_start_wait_matches_running_true_not_the_exit_code(roster, fleet, probes):
    down = dict(probes)
    down["ser6"] = probes["ser6"].__class__(**{**probes["ser6"].__dict__, "running": False})
    p = plan_set(roster, fleet, SetOptions(targets=["ser6/default/sfl-site/1/p1"], cockpit_host="rig2",
                                           self_pane="w4:p1", probes=down))
    up = next(s for s in p.steps if s.id.endswith(".up"))
    assert up.kind is StepKind.WAIT and up.via == "shell"
    assert '"running":true' in up.raw[2] and "status server --json" in up.raw[2]


# 3. resume carries the agent's own flags ------------------------------------

def test_resume_keeps_permission_mode_and_model():
    live = ["claude", "--dangerously-skip-permissions", "--resume"]          # as rolled on rig2
    assert resume.resume_argv("claude", "abc", live) == ["claude", "--dangerously-skip-permissions", "--resume", "abc"]
    haiku = ["claude", "--model", "haiku", "--dangerously-skip-permissions"]
    assert resume.resume_argv("claude", "abc", haiku) == [
        "claude", "--model", "haiku", "--dangerously-skip-permissions", "--resume", "abc"]


def test_resume_never_replays_a_prompt_or_an_old_selection():
    argv = ["claude", "--model", "haiku", "-r", "old-id", "-c", "-p", "--session-id", "u-1", "do the task"]
    assert resume.resume_argv("claude", "new", argv) == ["claude", "--model", "haiku", "--resume", "new"]
    assert resume.resume_argv("claude", "new", ["claude", "--permission-mode=plan", "fix it"]) == [
        "claude", "--permission-mode=plan", "--resume", "new"]


def test_resume_finds_the_agent_behind_an_interpreter():
    grok = ["node", "/home/u/.local/share/mise/installs/node/26.7.0/bin/grok", "--permission-mode=bypassPermissions"]
    assert resume.resume_argv("grok", "g1", grok) == ["grok", "--permission-mode=bypassPermissions", "--resume", "g1"]


def test_kinds_without_a_verified_flag_table_resume_bare():
    assert resume.resume_argv("codex", "c1", ["codex", "--full-auto"]) == ["codex", "resume", "c1"]
    assert resume.carried_flags("opencode", ["opencode", "--model", "x"]) == []


# 4. exec honours each step's own deadline -----------------------------------

def test_step_timeout_comes_from_the_step():
    assert X.step_timeout(("agent", "wait", "a", "--until", "idle", "--timeout", "90000"), 30.0) == 105.0
    loop = ("sh", "-c", "for i in $(seq 1 20); do x && exit 0; sleep 1; done; exit 1")
    assert X.step_timeout(loop, 60.0) == 55.0
    assert X.step_timeout(("pane", "list"), 30.0) == 30.0


# 5. reach is a transport check ----------------------------------------------

def test_reach_does_not_depend_on_a_running_mux(roster, fleet, probes):
    p = plan_set(roster, fleet, SetOptions(targets=["ser6/default/sfl-site/1/p1"], cockpit_host="rig2",
                                           self_pane="w4:p1", probes=probes))
    reach = next(s for s in p.steps if s.id.endswith("reach"))
    assert reach.raw == ("sh", "-c", "true") and reach.via == "shell" and not reach.mutating
