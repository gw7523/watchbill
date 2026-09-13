"""Recorded launch configuration: captured at park, restored or verified at resume."""
import json

from conftest import facts_from_fixture
from watchbill import agentconfig as A
from watchbill import collect, mux
from watchbill.plan import StepKind
from watchbill.plan_set import SetOptions, plan_set
from watchbill.slots import SlotStore

DISK = {"config_dir": "/home/u/.claude", "config_dir_exists": True, "settings_sha": "aaaa", "plugins": ["grok-build@x"],
        "hooks": {"PreToolUse": 1, "SessionStart": 1}, "mcp_servers": ["serena"], "trusted": True,
        "version": "2.1.267 (Claude Code)", "integration": "current (v8)", "permission_default": None, "model_default": "fable"}


def test_secrets_never_enter_the_record():
    env, secret = A.parse_environ("V CLAUDE_CONFIG_DIR=/seat\nV CLAUDE_CODE_OAUTH_TOKEN=sk-live\nN ANTHROPIC_API_KEY\nN FOO\nV HOME=/home/u\n")
    assert env == {"CLAUDE_CONFIG_DIR": "/seat", "HOME": "/home/u"}
    assert secret == ["ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"]
    body = A.environ_probe_argv(123)[2]
    assert body.startswith(A.READONLY_MARK) and "*TOKEN*" in body and "/proc/123/environ" in body


def test_secret_names_match_segments_not_substrings():
    env, secret = A.parse_environ("N GIT_AUTHOR_NAME\nN GIT_AUTHOR_EMAIL\nN GH_AUTH_TOKEN\nN STARSHIP_SESSION_KEY\nN OAUTH_FOO\n")
    assert secret == ["GH_AUTH_TOKEN", "STARSHIP_SESSION_KEY"]


def test_record_derives_permission_mode_model_and_seat():
    rec = A.build("claude", ["claude", "--model", "haiku", "--dangerously-skip-permissions", "do it"],
                  {"CLAUDE_CONFIG_DIR": "/home/u/.claude-work", "PATH": "/bin"}, ["ANTHROPIC_API_KEY"], DISK)
    assert rec["flags"] == ["--model", "haiku", "--dangerously-skip-permissions"]
    assert rec["permission_mode"] == "bypassPermissions" and rec["model"] == "haiku"
    assert rec["config_dir"] == "/home/u/.claude-work"
    assert A.restore_env(rec) == {"CLAUDE_CONFIG_DIR": "/home/u/.claude-work"}     # PATH recorded, not applied
    assert A.build("claude", ["claude"], {}, [], DISK)["model"] == "fable"            # falls back to settings
    assert A.build("grok", ["grok", "--permission-mode=bypassPermissions"], {}, [], {})["permission_mode"] == "bypassPermissions"


def test_drift_notes_and_hard_stops():
    same_notes, same_hard = A.diff(DISK, dict(DISK))
    assert same_notes == [] and same_hard == []
    changed = {**DISK, "plugins": ["grok-build@x", "new@y"], "version": "2.1.270 (Claude Code)", "settings_sha": "bbbb"}
    notes, hard = A.diff(DISK, changed)
    assert hard == [] and any("+new@y" in n for n in notes) and any("2.1.270" in n for n in notes)
    assert any("trust" in h for h in A.diff(DISK, {**DISK, "trusted": False})[1])
    assert any("integration" in h for h in A.diff(DISK, {**DISK, "integration": "not installed"})[1])
    assert any("no longer exists" in h for h in A.diff(DISK, {**DISK, "config_dir_exists": False})[1])


def test_roster_carries_the_record_and_schema_accepts_it(fleet_json, allowlist):
    facts = facts_from_fixture(fleet_json)
    sf = facts[1].sessions[0]                                   # ser6
    sf.agent_env["w1:p1"] = ({"CLAUDE_CONFIG_DIR": "/home/u/.claude-sfl"}, ["ANTHROPIC_API_KEY"])
    sf.agent_disk["w1:p1"] = DISK
    r = collect.build_roster("t", facts, slots=SlotStore(), allowlist=allowlist)
    o = r.by_human("ser6/default/sfl-site/1/p1")
    assert o.agent_config["config_dir"] == "/home/u/.claude-sfl" and o.agent_config["disk"]["plugins"] == ["grok-build@x"]
    assert r.by_human("ser6/default/sfl-site/1/p2").agent_config is None      # not an agent
    import jsonschema
    from pathlib import Path
    schema = json.loads((Path(__file__).parent.parent / "docs/roster.schema.json").read_text())
    jsonschema.Draft202012Validator(schema).validate(r.to_dict())


def test_set_restores_seat_env_and_verifies_before_start(fleet_json, allowlist, fleet, probes):
    facts = facts_from_fixture(fleet_json)
    sf = facts[1].sessions[0]
    sf.agent_env["w1:p1"] = ({"CLAUDE_CONFIG_DIR": "/home/u/.claude-sfl"}, [])
    sf.agent_disk["w1:p1"] = DISK
    r = collect.build_roster("t", facts, slots=SlotStore(), allowlist=allowlist)
    p = plan_set(r, fleet, SetOptions(targets=["ser6/default/sfl-site/1/p1"], cockpit_host="rig2", self_pane="w4:p1", probes=probes))
    ws = next(s for s in p.steps if s.verb == ("workspace", "create"))
    assert ("--env", "CLAUDE_CONFIG_DIR=/home/u/.claude-sfl") == ws.raw[ws.raw.index("--env"):ws.raw.index("--env") + 2]
    check = next(s for s in p.steps if s.kind is StepKind.CHECK)
    start = next(s for s in p.steps if s.verb == ("agent", "start"))
    assert p.steps.index(check) < p.steps.index(start)
    # a restored (reused) pane never got --env: the seat is exported into its shell before the start
    env = next(s for s in p.steps if s.id.endswith(".env"))
    assert p.steps.index(env) < p.steps.index(start) and env.raw[-1] == "export CLAUDE_CONFIG_DIR=/home/u/.claude-sfl"
    assert check.expect == DISK and not check.mutating and "/home/u/.claude-sfl" in check.raw[2]


def test_exec_check_reports_drift_and_stops_on_hard_drift(fleet, tmp_path):
    from watchbill import exec as X
    from watchbill.journal import Journal
    from watchbill.plan import Plan, Step
    from watchbill.transport.base import CmdResult

    class Live:
        backend = mux.get("herdr")
        def __init__(self, out):
            self.out, self.calls = out, []
        def shell(self, argv, timeout=60.0):
            self.calls.append(argv)
            return CmdResult(tuple(argv), 0, json.dumps(self.out))
        def mux(self, *a, timeout=30.0):
            self.calls.append(a)
            return CmdResult(a, 0, "{}")
    def plan_with(out):
        plan = Plan(verb="set", fleet="t", approved=True)
        plan.add(Step(id="c", kind=StepKind.CHECK, host="ser6", session="default", description="check",
                      raw=("sh", "-c", ": watchbill-readonly; probe"), via="shell", expect=DISK))
        plan.add(Step(id="s", kind=StepKind.HERDR, host="ser6", session="default", description="start",
                      raw=("agent", "start", "a", "--kind", "claude", "--pane", "w1:p1"), via="mux", mutating=True))
        return plan
    printed = []
    soft = Live({**DISK, "plugins": ["grok-build@x", "new@y"]})
    res = X.Executor(fleet, Journal(tmp_path / "j"), run_id="1", session_factory=lambda h, s: soft, out=printed.append).run(plan_with(soft.out))
    assert res.code == 0 and any("+new@y" in line for line in printed)
    hard = Live({**DISK, "trusted": False})
    res = X.Executor(fleet, Journal(tmp_path / "k"), run_id="2", session_factory=lambda h, s: hard, out=printed.append).run(plan_with(hard.out))
    assert res.failed and "trust" in res.failed[0]
    assert not any(c[:2] == ("agent", "start") for c in hard.calls)          # the agent was not started


def test_herdr_agent_start_passes_arguments_not_the_executable():
    be = mux.get("herdr")
    argv = be.agent_start("a", "claude", "w1:p1", ["claude", "--model", "haiku", "--resume", "id"])[0]
    assert argv[argv.index("--"):] == ["--", "--model", "haiku", "--resume", "id"]
    fresh = be.agent_start("a", "claude", "w1:p1", None, ["--dangerously-skip-permissions"])[0]
    assert fresh[fresh.index("--"):] == ["--", "--dangerously-skip-permissions"]


def test_opencode_config_paths_are_restored_but_inline_content_is_not():
    env, _ = A.parse_environ("V OPENCODE_CONFIG=/tmp/oc.json\nV OPENCODE_CONFIG_CONTENT={\"apiKey\":\"x\"}\n")
    rec = A.build("opencode", ["opencode"], env, [], {})
    assert A.restore_env(rec) == {"OPENCODE_CONFIG": "/tmp/oc.json"}
    assert "OPENCODE_CONFIG_CONTENT" not in A.environ_probe_argv(1)[2]
