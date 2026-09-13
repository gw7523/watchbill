"""Found while building the tmux example (lane 6)."""
import pytest

from watchbill import mux
from watchbill.classify import classify

from test_mux import mixed_fleet, tmux_facts, tmux_roster  # noqa: F401  (fixtures)


def test_an_agent_behind_an_interpreter_is_still_that_agent():
    pi = {"foreground_processes": [{"argv": ["node", "/home/u/.local/share/mise/installs/node/26.7.0/bin/grok",
                                             "--permission-mode=bypassPermissions"], "cmdline": "", "cwd": "/tmp", "name": "node", "pid": 1}]}
    c = classify({"pane_id": "%1"}, pi)
    assert (c.role, c.kind) == ("agent", "grok")
    plain = {"foreground_processes": [{"argv": ["node", "server.js"], "cmdline": "", "cwd": "/", "name": "node", "pid": 2}]}
    assert classify({"pane_id": "%2"}, plain).role != "agent"


def test_tmux_exit_waits_for_the_shell_not_for_a_different_name():
    be = mux.get("tmux")
    loop = be.resolve_poll("demo", be.agent_wait_exit("%1", "grok", 20000))[2]
    assert '[ "$c" = bash ]' in loop and '[ "$c" = zsh ]' in loop and "!= grok" not in loop


def test_verify_runs_after_the_restart_in_a_window_that_stops_the_session(roster, fleet, probes):
    from watchbill.plan_relieve import RelieveOptions, plan_relieve
    roster.by_human("ser6/default/sfl-site/1/p1").agent_status = "idle"
    p = plan_relieve(roster, fleet, RelieveOptions(action="restart-harness", hosts=["ser6"], cockpit_host="rig2", probes=probes))
    ids = [s.id for s in p.steps]
    assert ids.index("ser6.stop1") < ids.index("ser6.set1.start") < ids.index("ser6.verify1")
    live = plan_relieve(roster, fleet, RelieveOptions(action="upgrade-agents", hosts=["ser6"], action_options={"kinds": ["grok"]},
                                                      cockpit_host="rig2", probes=probes))
    lids = [s.id for s in live.steps]
    assert lids.index("ser6.verify1") < lids.index("ser6.done")         # no stop: verify stays right after the commands


def run_condition(cond: str, c: str, q: int) -> bool:
    """Execute a poll condition in a real sh with the loop's variables set."""
    import subprocess
    return subprocess.run(["sh", "-c", f'c={c}; q={q}; {cond} && exit 0; exit 1'], capture_output=True).returncode == 0


def test_tmux_poll_conditions_actually_run_in_sh():
    """String checks let `[ a ] [ b ]` through ("too many arguments" at run time); execute them."""
    be = mux.get("tmux", idle_after_s=6)
    idle = be.agent_wait_idle("%1", 90000)[2]
    assert run_condition(idle, "claude", 10) is True           # agent up and quiet
    assert run_condition(idle, "claude", 2) is False           # still producing output
    assert run_condition(idle, "bash", 60) is False            # the agent is not running at all
    gone = be.agent_wait_exit("%1", "grok", 20000)[2]
    assert run_condition(gone, "bash", 0) is True and run_condition(gone, "node", 0) is False


def test_park_and_prompt_clear_pending_input_first(tmux_roster, mixed_fleet, probes):
    from watchbill.plan_secure import SecureOptions, plan_secure
    from watchbill.plan_set import SetOptions, plan_set
    p = plan_secure(tmux_roster, mixed_fleet, SecureOptions(targets=["mac/default/alpha/edit/p1"], cockpit_host="rig2"))
    raws = [s.raw for s in p.steps]
    assert raws.index(("send-keys", "-t", "%0", "C-u")) < raws.index(("send-keys", "-t", "%0", "-l", "/exit"))
    o = tmux_roster.by_human("mac/default/alpha/edit/p1")
    o.resume_prompt = type(o.resume_prompt)("pinned", "carry on"); o.resume_argv = ["claude", "--continue"]
    sp = plan_set(tmux_roster, mixed_fleet, SetOptions(targets=[o.human_id], cockpit_host="rig2", self_pane="w4:p1",
                                                        probes={"mac": probes["vps"]}, live=tmux_roster))
    ids = [s.id for s in sp.steps]
    assert ids.index(next(i for i in ids if i.endswith(".clr"))) < ids.index(next(i for i in ids if i.endswith(".prompt")))
    assert mux.get("herdr").clear_input("w1:p1") == ["pane", "send-text", "w1:p1", "\x15"]


def test_post_prompt_check_uses_the_resolved_pane(mixed_fleet, tmp_path):
    from watchbill import exec as X
    from watchbill.journal import Journal
    from watchbill.plan import Plan, Step, StepKind
    from watchbill.transport.base import CmdResult

    class Tmux:
        backend = mux.get("tmux")
        def __init__(self): self.calls = []
        def mux(self, *a, timeout=30.0):
            self.calls.append(a)
            assert not any("{pane:" in t for t in a), f"placeholder reached tmux: {a}"
            return CmdResult(a, 0, "$ ")
        def shell(self, argv, timeout=60.0): return CmdResult(tuple(argv), 0, "")
    fake = Tmux()
    plan = Plan(verb="set", fleet="t", approved=True)
    plan.add(Step(id="p", kind=StepKind.HERDR, host="mac", session="default", description="prompt", mux="tmux",
                  raw=("send-keys", "-t", "{pane:S1}", "-l", "hi"), via="mux", mutating=True, placeholders=True,
                  precondition="agent_status != blocked", slot_id="S1", phase="restore"))
    ex = X.Executor(mixed_fleet, Journal(tmp_path / "j"), run_id="r", session_factory=lambda h, s: fake, out=lambda *_: None)
    res = X.ExecResult(); res.pane_map["S1"] = "%7"
    assert ex._run_step(plan.steps[0], plan, res) is None
    assert ("capture-pane", "-p", "-t", "%7", "-S", "-15") in fake.calls


def test_a_stop_window_restores_watchers_and_shells_not_only_parked_agents(roster, fleet, probes):
    from watchbill.plan_relieve import RelieveOptions, plan_relieve
    roster.by_human("ser6/default/sfl-site/1/p1").agent_status = "idle"
    p = plan_relieve(roster, fleet, RelieveOptions(action="restart-harness", hosts=["ser6"], cockpit_host="rig2", probes=probes))
    dev = roster.by_human("ser6/default/sfl-site/1/p2")                     # `npm run dev`, allowlisted
    assert any(s.slot_id == dev.slot_id and s.verb == ("pane", "run") for s in p.steps)
    shell = roster.by_human("ser6/default/scratch/1/p2")                    # plain shell: its pane comes back
    assert any(s.creates == shell.slot_id for s in p.steps)


def test_viewport_attach_is_opt_in(roster, fleet, probes):
    from watchbill.plan_set import SetOptions, plan_set
    opts = dict(targets=["ser6/default/sfl-site/1/p1"], cockpit_host="rig2", self_pane="w4:p1", probes=probes)
    assert any(s.id.endswith("att2") for s in plan_set(roster, fleet, SetOptions(**opts)).steps)     # opted in (conftest)
    fleet.host("ser6").mux_options = {}
    assert not any("att" in s.id for s in plan_set(roster, fleet, SetOptions(**opts)).steps)         # default: no viewport


def test_tmux_process_probe_is_portable_and_always_succeeds():
    be = mux.get("tmux")
    body = be.process_info_argv(mux.MuxPane(pane_id="%0", workspace_id="$0", tab_id="@0", pid=89151, tty="/dev/ttys006"))[2]
    assert "--sort" not in body and "lsof -a -p" in body and body.rstrip().endswith("true")
    # BSD ps output as captured on the Mac mini (macOS 26.4), then the lsof cwd line
    out = "89151 89150 Ss   -bash\n89538 89151 S+   claude --model haiku --dangerously-skip-permissions\n/private/tmp/wb-mac-t\n"
    info = be.parse_process_info(mux.MuxPane(pane_id="%0", workspace_id="$0", tab_id="@0", pid=89151, tty="/dev/ttys006", cwd="/tmp"), out)
    assert info["foreground_processes"][0]["argv"][0] == "claude" and info["foreground_processes"][0]["cwd"] == "/private/tmp/wb-mac-t"


def test_the_agent_argv_is_the_agents_own_process_not_a_helper():
    pi = {"foreground_processes": [
        {"argv": ["caffeinate", "-i", "-t", "300"], "cmdline": "", "cwd": "/tmp", "name": "caffeinate", "pid": 1},
        {"argv": ["claude", "--model", "haiku", "--dangerously-skip-permissions"], "cmdline": "", "cwd": "/tmp", "name": "2.1.270", "pid": 2}]}
    c = classify({"pane_id": "w1:p1", "agent": "claude"}, pi)
    assert c.argv[0] == "claude" and "--dangerously-skip-permissions" in c.argv


def test_remote_control_refuses_by_default_and_disconnects_when_opted_in(fleet, tmp_path):
    from watchbill import exec as X
    from watchbill.journal import Journal
    from watchbill.plan import Plan, Step, StepKind
    from watchbill.transport.base import CmdResult

    class RC:
        backend = mux.get("herdr")
        def __init__(self): self.calls, self.active = [], True
        def mux(self, *a, timeout=30.0):
            self.calls.append(a)
            if a[:2] == ("pane", "read"):
                return CmdResult(a, 0, "❯\n  ⏸ manual mode on · ? for shortcuts" + ("   /rc active" if self.active else ""))
            if a[:2] == ("pane", "send-keys") and a[-1] == "enter" and ("pane", "send-keys", a[2], "up") in self.calls:
                self.active = False
            return CmdResult(a, 0, "{}")
        def shell(self, argv, timeout=60.0): return CmdResult(tuple(argv), 0, "")
    def plan():
        p = Plan(verb="secure", fleet="t", approved=True)
        p.add(Step(id="park", kind=StepKind.HERDR, host="ser6", session="s", description="/exit", slot_id="S",
                   raw=("pane", "send-text", "w1:p1", "/exit"), via="mux", mutating=True,
                   precondition="claude remote control disconnected"))
        return p
    fake = RC()
    res = X.Executor(fleet, Journal(tmp_path / "a"), run_id="1", session_factory=lambda h, s: fake, out=lambda *_: None,
                     sleep=lambda s: None).run(plan())
    assert res.failed and "Remote Control is connected" in res.failed[0]
    assert ("pane", "send-text", "w1:p1", "/exit") not in fake.calls                       # nothing typed
    fleet.host("ser6").mux_options = {"disconnect_remote_control": True}
    fake2 = RC()
    res = X.Executor(fleet, Journal(tmp_path / "b"), run_id="2", session_factory=lambda h, s: fake2, out=lambda *_: None,
                     sleep=lambda s: None).run(plan())
    assert not res.failed and ("pane", "send-text", "w1:p1", "/rc") in fake2.calls
    assert fake2.calls.index(("pane", "send-text", "w1:p1", "/rc")) < fake2.calls.index(("pane", "send-text", "w1:p1", "/exit"))
