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


def test_herdr_verbs_match_what_was_probed_on_0_8_2():
    """Regression pins for the four bugs the live probe caught (see
    docs/herdr-0.8.2-facts.md, "Probed 2026-09-11")."""
    be = M.get("herdr")
    # 1. `ctrl-c` is rejected with invalid_key; `C-c` is the accepted name
    assert be.interrupt("w1:p1")[-1] == "C-c"
    # 2. `agent wait` is level-triggered, so waiting only for `idle` hangs on a
    #    `done` agent; every settled state is matched and blocked is refused later
    idle = be.agent_wait_idle("a", 90000)
    assert [idle[i + 1] for i, t in enumerate(idle) if t == "--until"] == ["idle", "done", "blocked"]
    # 3. an exited agent is *absent*, not `unknown`: the oracle polls `agent get`
    assert be.agent_wait_exit("w1:p1", "claude", 20000)[:1] == [M.base.POLL]
    loop = be.resolve_poll("default", be.agent_wait_exit("w1:p1", "claude", 20000))
    assert loop[:2] == ["sh", "-c"] and "agent get w1:p1" in loop[2] and "|| exit 0" in loop[2]
    assert "--until unknown" not in " ".join(be.agent_wait_exit("w1:p1", "claude", 1))
    # 4. `--source recent` is empty on a settled pane; excerpts use the viewport
    assert "visible" in be.excerpt_argv("w1:p1", 40)
    # headless start is verified, so the backend offers it
    assert be.session_start("s", "l", "~") == ["server"]


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


def test_tmux_dismiss_is_a_planned_kill_server(tmux_roster, mixed_fleet):
    """On tmux the session IS the server, so the declared stop is `kill-server`.
    That planned stop must pass the never-emit gate — otherwise every cold tmux
    window would need --force-server-stop, which the skill forbids without the
    human saying it. An *unplanned* kill-server is still gated."""
    p = plan_secure(tmux_roster, mixed_fleet, opts(mode="dismiss", targets=["beta"], host="mac"))
    stop = next(s for s in p.steps if s.raw == ("kill-server",))
    assert stop.mutating and stop.planned_stop
    assert check_verbs_allowed(p.steps) == []
    from dataclasses import replace
    rogue = replace(stop, id="rogue", planned_stop=False)
    assert check_verbs_allowed([rogue]) and not check_verbs_allowed([rogue], force_server_stop=True)
    # herdr `server stop` stays gated no matter what
    from watchbill.plan import Step, StepKind
    hs = Step(id="h", kind=StepKind.HERDR, host="rig2", raw=("server", "stop"), via="mux", mux="herdr",
              description="", mutating=True, planned_stop=True)
    assert check_verbs_allowed([hs])


def test_tmux_set_no_viewport_layout_reapplied(tmux_roster, mixed_fleet, probes):
    pr = dict(probes)
    pr["mac"] = probes["vps"].__class__(**{**probes["vps"].__dict__, "host": "mac", "running": False, "flavor": "brew", "handoff_supported": False})
    p = plan_set(tmux_roster, mixed_fleet, SetOptions(targets=["alpha"], host="mac", cockpit_host="rig2", self_pane="w4:p1", probes=pr))
    ids = [s.id for s in p.steps]
    assert not any("att" in i for i in ids)                                  # tmux needs no #2064 viewport
    start = next(s for s in p.steps if s.id == "set1.start")
    # the start step IS the first workspace: it creates that occupant's slot, so
    # {pane:<slot>} / {ws:<slot>} resolve from it and no second new-session runs
    assert start.raw[:4] == ("new-session", "-d", "-s", "alpha")
    assert start.creates == tmux_roster.by_human("mac/default/alpha/edit/p1").slot_id
    assert "-n" in start.raw and start.raw[start.raw.index("-n") + 1] == "edit"   # select-layout needs the name
    assert len([s for s in p.steps if s.raw[:3] == ("new-session", "-d", "-s")]) == 1
    assert not any(t.startswith("{pane:") or t.startswith("{ws:")
                   for s in p.steps for t in s.raw if s.creates is None and not s.placeholders)
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


# -- execution: the gap round 2 named (plans were only ever shape-checked) ----

class FakeTmux:
    """A tmux server that answers the `-P -F` creates and records argv.
    Enough to run a whole cold `set` through the Executor."""

    def __init__(self, host, session):
        from watchbill.transport.base import backend_for
        self.host, self.session, self.backend = host, session, backend_for(host)
        self.calls: list[tuple[str, ...]] = []
        self.n = 0

    def mux_argv(self, *a):
        return [*self.backend.cli_prefix(self.session), *a]

    herdr_argv = mux_argv

    def shell_argv(self, argv):
        return list(argv)

    def mux(self, *a, timeout=30.0):
        from watchbill.transport.base import CmdResult
        self.calls.append(tuple(a))
        argv = tuple(self.mux_argv(*a))
        assert not any("{pane:" in t or "{ws:" in t for t in a), f"unresolved placeholder reached tmux: {a}"
        if a[:1] == ("new-session",):
            self.n += 1
            return CmdResult(argv, 0, f"$9|%{self.n}\n")
        if a[:1] == ("new-window",):
            self.n += 1
            return CmdResult(argv, 0, f"@9|%{self.n}\n")
        if a[:1] == ("split-window",):
            self.n += 1
            return CmdResult(argv, 0, f"%{self.n}\n")
        if a[:1] == ("display-message",):
            return CmdResult(argv, 0, "3.7c|/tmp/tmux-1000/default|1\n")
        if a[:1] == ("capture-pane",):
            return CmdResult(argv, 0, "$ ")
        return CmdResult(argv, 0, "")

    herdr = mux

    def shell(self, argv, timeout=60.0):
        from watchbill.transport.base import CmdResult
        self.calls.append(("shell", *argv))
        assert not any("{pane:" in t or "{ws:" in t for t in argv), f"unresolved placeholder in shell: {argv}"
        return CmdResult(tuple(argv), 0, "")

    def reachable(self):
        return True


def test_executor_runs_a_whole_tmux_cold_set(tmux_roster, mixed_fleet, probes, tmp_path):
    """Round 2 blocker 1: a cold tmux `set` must resolve every {pane:}/{ws:}
    placeholder at run time. The plan shape alone never proved this because no
    test had executed a tmux plan."""
    from watchbill import exec as X
    from watchbill.journal import Journal
    made: dict = {}

    def factory(host, session):
        return made.setdefault((host.name, session), FakeTmux(host, session))

    pr = {"mac": probes["vps"].__class__(**{**probes["vps"].__dict__, "host": "mac", "running": False, "flavor": "brew"})}
    plan = plan_set(tmux_roster, mixed_fleet, SetOptions(targets=["alpha"], host="mac", cockpit_host="rig2",
                                                         self_pane="w4:p1", probes=pr, approved=True, no_prompt=True))
    res = X.Executor(mixed_fleet, Journal(tmp_path / "j.jsonl"), run_id="t1", session_factory=factory).run(plan)
    assert res.code == 0 and not res.failed, res.failed
    calls = made[("mac", "default")].calls
    assert len([c for c in calls if c[:1] == ("new-session",)]) == 1
    # every slot the plan restored got a real pane id
    for slot in {s.creates for s in plan.steps if s.creates and not s.creates.startswith(("boot:", "viewport:"))}:
        assert res.pane_map[slot].startswith("%")
    assert any(c[:1] == ("select-layout",) for c in calls)


def test_tmux_relieve_cold_starts_the_session_once(tmux_roster, mixed_fleet, probes):
    """Round 2 blocker 2: start_steps + set_steps both used to emit
    `new-session -s alpha`, and the second would fail as a duplicate."""
    tmux_roster.by_human("mac/default/alpha/logs/p1").agent_status = "idle"
    pr = {"mac": probes["vps"].__class__(**{**probes["vps"].__dict__, "host": "mac", "flavor": "brew", "handoff_supported": False})}
    p = plan_relieve(tmux_roster, mixed_fleet, RelieveOptions(action="upgrade-mux", hosts=["mac"], cockpit_host="rig2", probes=pr))
    news = [s for s in p.steps if s.raw[:1] == ("new-session",)]
    assert len(news) == 1, [s.id for s in news]
    ids = [s.id for s in p.steps]
    assert ids.index("mac.stop1") < ids.index(news[0].id)
    assert check_verbs_allowed(p.steps) == []          # the planned kill-server is sanctioned


def test_no_herdr_only_verbs_reach_a_tmux_host(tmux_roster, mixed_fleet, probes):
    """Round 2 blocker 3: RemoteCmd(via=...) used to be replayed against the
    host's mux, turning `herdr integration install` into `tmux integration
    install` and the default verify into `tmux status server --json`."""
    pr = {"mac": probes["vps"].__class__(**{**probes["vps"].__dict__, "host": "mac", "flavor": "brew",
                                            "agent_versions": {"claude": "2.1.267"}, "agent_flavors": {"claude": "brew"}})}
    p = plan_relieve(tmux_roster, mixed_fleet, RelieveOptions(action="upgrade-agents", hosts=["mac"],
                                                              action_options={"kinds": ["claude"]}, cockpit_host="rig2",
                                                              probes=pr, force=True))
    for s in p.steps:
        if s.mux == "tmux" and s.via == "mux":
            assert s.raw[0] in ("new-session", "new-window", "split-window", "select-layout", "send-keys",
                                "respawn-pane", "kill-server", "kill-session", "display-message", "capture-pane",
                                "source-file", "list-sessions", "list-windows", "list-panes"), s.raw
    assert not any("integration" in s.raw for s in p.steps)
    verify = [s for s in p.steps if s.id.startswith("mac.verify")]
    assert verify and verify[0].raw[:1] == ("display-message",)


def test_blocked_check_uses_the_occupants_kind(tmux_roster, mixed_fleet, probes):
    """Round 2 blocker 5: exec re-read the screen with kind=None, so the
    kind-specific approval patterns (Claude's `❯ 1. Yes`) never matched."""
    from watchbill import exec as X
    from watchbill.journal import Journal
    from watchbill.plan import StepKind
    o = tmux_roster.by_human("mac/default/alpha/edit/p1")
    o.resume_prompt = type(o.resume_prompt)("pinned", "carry on")
    o.resume_argv = ["claude", "--continue"]
    p = plan_set(tmux_roster, mixed_fleet, SetOptions(targets=[o.human_id], cockpit_host="rig2", self_pane="w4:p1",
                                                      probes={"mac": probes["vps"]}, live=tmux_roster, approved=True))
    prompt = next(s for s in p.steps if s.precondition == "agent_status != blocked")
    assert prompt.agent_kind == "claude"
    # the idle wait insists on quiet, not merely "a binary is running"
    wait = next(s for s in p.steps if s.kind is StepKind.WAIT and s.via == "shell")
    assert '"$q" -ge 30' in wait.raw[2] and "window_activity" in wait.raw[2]

    class Blocked(FakeTmux):
        def mux(self, *a, timeout=30.0):
            if a[:1] == ("capture-pane",):
                from watchbill.transport.base import CmdResult
                return CmdResult(tuple(a), 0, "Do you want to proceed?\n❯ 1. Yes\n  2. No\n")
            return super().mux(*a, timeout=timeout)
    fake = Blocked(mixed_fleet.host("mac"), "default")
    res = X.Executor(mixed_fleet, Journal(tmp_path_factory()), run_id="b1", session_factory=lambda h, s: fake).run(p)
    assert any("blocked" in f for f in res.failed), res.failed
    assert not any(c[:1] == ("send-keys",) and "carry on" in c for c in fake.calls)


def tmp_path_factory():
    import tempfile
    from pathlib import Path
    return Path(tempfile.mkdtemp()) / "j.jsonl"
