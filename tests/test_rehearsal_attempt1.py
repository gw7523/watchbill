"""Pins for what attempt 1 of the live rehearsal found (lane 3)."""
import json

from watchbill import exec as X
from watchbill import mux
from watchbill.journal import Journal
from watchbill.plan import Plan, Step, StepKind
from watchbill.transport.base import CmdResult, agent_session_env, run_argv


def test_a_driving_agents_session_never_leaks_into_what_watchbill_starts(monkeypatch):
    """Root cause of the failed resume: the server inherited CLAUDE_CODE_CHILD_SESSION,
    so the agent inside it was a child session with no transcript."""
    for k, v in {"CLAUDECODE": "1", "CLAUDE_CODE_CHILD_SESSION": "1", "CLAUDE_CODE_SESSION_ID": "x",
                 "GROK_CC_SESSION_ID": "y", "HERDR_PANE_ID": "w4:p1", "CLAUDE_CONFIG_DIR": "/seat"}.items():
        monkeypatch.setenv(k, v)
    got = run_argv(["sh", "-c", "env"], timeout=10).stdout
    for leaked in ("CLAUDECODE=", "CLAUDE_CODE_CHILD_SESSION=", "CLAUDE_CODE_SESSION_ID=", "GROK_CC_SESSION_ID=", "HERDR_PANE_ID="):
        assert leaked not in got, leaked
    assert "CLAUDE_CONFIG_DIR=/seat" in got            # seat config is NOT session identity: kept
    assert set(agent_session_env({"CLAUDE_CODE_X": "", "CLAUDE_CONFIG_DIR": "", "PATH": ""})) == {"CLAUDE_CODE_X"}


def test_agent_start_has_its_own_deadline_shorter_than_execs():
    argv = mux.get("herdr").agent_start("a", "claude", "w1:p1", ["claude", "--resume", "id"])[0]
    assert argv[argv.index("--timeout") + 1] == "60000"
    assert X.step_timeout(tuple(argv), 30.0) == 75.0


class Restored:
    """A herdr server that restored workspace `rehearse` (one pane) on restart."""
    backend = mux.get("herdr")

    def __init__(self):
        self.calls = []
        snap = {"workspaces": [{"workspace_id": "w1", "label": "rehearse", "number": 1}],
                "tabs": [{"tab_id": "w1:t1", "workspace_id": "w1", "label": "1", "number": 1}],
                "layouts": [{"tab_id": "w1:t1", "workspace_id": "w1", "panes": [{"pane_id": "w1:p1", "rect": {}}], "splits": []}],
                "panes": [{"pane_id": "w1:p1", "tab_id": "w1:t1", "workspace_id": "w1", "cwd": "/w"}]}
        self.snapshot = json.dumps({"id": "x", "result": {"snapshot": snap}})

    def mux(self, *a, timeout=30.0):
        self.calls.append(a)
        if a[:2] == ("api", "snapshot"):
            return CmdResult(a, 0, self.snapshot)
        if a[:2] == ("agent", "start"):
            return CmdResult(a, 1, "", '{"error":{"code":"timeout"}}')
        if a[:2] == ("pane", "read"):
            return CmdResult(a, 0, "~/Work ❯ claude --resume d9\nNo conversation found with session ID: d9\n~/Work ✗\n")
        return CmdResult(a, 0, '{"id":"x","result":{"root_pane":{"pane_id":"w2:p1"},"workspace":{"workspace_id":"w2"}}}')

    def shell(self, argv, timeout=60.0):
        return CmdResult(tuple(argv), 0, "")


def test_exec_reuses_a_restored_workspace_instead_of_duplicating_it(fleet, tmp_path):
    fake = Restored()
    plan = Plan(verb="set", fleet="t", approved=True)
    plan.add(Step(id="ws", kind=StepKind.HERDR, host="ser6", session="default", description="create",
                  raw=("workspace", "create", "--label", "rehearse", "--cwd", "/w", "--no-focus"), via="mux", mutating=True,
                  creates="SLOT1", reuse={"workspace": "rehearse", "tab": "1", "index": 0}))
    plan.add(Step(id="ws2", kind=StepKind.HERDR, host="ser6", session="default", description="create another",
                  raw=("workspace", "create", "--label", "other", "--cwd", "/w", "--no-focus"), via="mux", mutating=True,
                  creates="SLOT2", reuse={"workspace": "other", "tab": "1", "index": 0}))
    res = X.Executor(fleet, Journal(tmp_path / "j"), run_id="r", session_factory=lambda h, s: fake, out=lambda *_: None).run(plan)
    assert res.pane_map["SLOT1"] == "w1:p1" and res.pane_map["ws:SLOT1"] == "w1"       # reused, not created
    assert res.pane_map["SLOT2"] == "w2:p1"                                            # no match → created
    creates = [c for c in fake.calls if c[:2] == ("workspace", "create")]
    assert creates == [("workspace", "create", "--label", "other", "--cwd", "/w", "--no-focus")]


def test_a_failed_agent_start_says_why(fleet, tmp_path):
    fake = Restored()
    plan = Plan(verb="set", fleet="t", approved=True)
    plan.add(Step(id="s", kind=StepKind.HERDR, host="ser6", session="default", description="start",
                  raw=("agent", "start", "a", "--kind", "claude", "--pane", "w1:p1", "--timeout", "60000", "--", "--resume", "d9"),
                  via="mux", mutating=True))
    res = X.Executor(fleet, Journal(tmp_path / "j"), run_id="r", session_factory=lambda h, s: fake, out=lambda *_: None).run(plan)
    assert res.failed and "No conversation found" in res.failed[0]
