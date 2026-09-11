from watchbill import prompts
from watchbill.plan_set import SetOptions, agent_name, plan_set

SER6_BLOCKED = "ser6/default/sfl-site/1/p1"
SER6_DEV = "ser6/default/sfl-site/1/p2"
RIG2_CODEX = "rig2/default/api#3/1/p1"
RIG2_WATCH = "rig2/default/api/1/p2"


def opts(probes, **kw):
    base = dict(cockpit_host="rig2", self_pane="w4:p1", probes=probes)
    base.update(kw)
    return SetOptions(**base)


def idx(plan, pred):
    return next(i for i, s in enumerate(plan.steps) if pred(s))


def test_attach_precedes_agent_start_and_resume_argv(roster, fleet, probes):
    p = plan_set(roster, fleet, opts(probes, targets=[SER6_BLOCKED, SER6_DEV]))
    assert not p.refused
    i_att = idx(p, lambda s: s.id.endswith("att2"))
    i_start = idx(p, lambda s: s.verb == ("agent", "start"))
    i_ws = idx(p, lambda s: s.verb == ("workspace", "create"))
    assert idx(p, lambda s: s.verb == ("status", "server")) < i_att < i_ws < i_start
    start = p.steps[i_start]
    assert start.raw[-3:] == ("claude", "--resume", "b1b1b1b1-0000-4000-8000-0000000000b1")
    assert "--kind" in start.raw and start.raw[start.raw.index("--kind") + 1] == "claude"
    assert start.placeholders and any(t.startswith("{pane:") for t in start.raw)
    assert "#2064" in p.steps[i_att].description


def test_unref_agent_starts_fresh_then_prompts(roster, fleet, probes):
    o = roster.by_human(RIG2_CODEX)
    assert o.unref and o.resume_argv is None and o.kind == "codex"
    o.resume_prompt = prompts.ResumePrompt("pinned", "finish the codex tests")
    p = plan_set(roster, fleet, opts(probes, targets=[RIG2_CODEX], include_local=True))
    start = next(s for s in p.steps if s.verb == ("agent", "start"))
    assert "--" not in start.raw
    wait = next(s for s in p.steps if s.verb == ("agent", "wait"))
    prompt = next(s for s in p.steps if s.verb == ("agent", "prompt"))
    assert p.steps.index(wait) < p.steps.index(prompt)
    assert wait.raw[2:5] == (agent_name(o), "--until", "idle")
    assert prompt.precondition == "agent_status != blocked" and prompt.raw[3] == "finish the codex tests"


def test_no_prompt_and_missing_prompt(roster, fleet, probes):
    p = plan_set(roster, fleet, opts(probes, targets=[RIG2_CODEX], include_local=True))
    assert not any(s.verb == ("agent", "prompt") for s in p.steps)
    assert any("no resume prompt" in n for n in p.notes)
    p = plan_set(roster, fleet, opts(probes, targets=[RIG2_CODEX], include_local=True, no_prompt=True))
    assert not any("no resume prompt" in n for n in p.notes)


def test_watchers_relaunch_only_when_allowlisted(roster, fleet, probes):
    p = plan_set(roster, fleet, opts(probes, targets=[RIG2_WATCH, SER6_DEV], include_local=True))
    runs = [s for s in p.steps if s.verb == ("pane", "run") and s.slot_id]   # viewport attaches also use pane run
    assert len(runs) == 2                          # watchexec and `npm run dev` are both allowlisted in conftest
    assert runs[0].raw[3:] == ("watchexec", "-w", "src", "--", "pytest", "-q")
    roster.by_human(SER6_DEV).allow_relaunch = False
    p = plan_set(roster, fleet, opts(probes, targets=[SER6_DEV]))
    assert not any(s.verb == ("pane", "run") and s.slot_id for s in p.steps)
    assert any("not in allowlist" in n for n in p.notes)


def test_bridge_never_relaunched(roster, fleet, probes):
    p = plan_set(roster, fleet, opts(probes, targets=["rig2/default/bridge/1/p1"], include_local=True))
    slot = roster.by_human("rig2/default/bridge/1/p1").slot_id
    assert not any(s.slot_id == slot and s.verb in (("agent", "start"), ("pane", "run")) for s in p.steps)
    assert any("never relaunched" in n for n in p.notes)


def test_reuses_live_workspace_when_present(roster, fleet, probes):
    p = plan_set(roster, fleet, opts(probes, targets=[SER6_BLOCKED], live=roster))
    assert not any(s.verb == ("workspace", "create") for s in p.steps)
    start = next(s for s in p.steps if s.verb == ("agent", "start"))
    assert "w1:p1" in start.raw and not start.placeholders


def test_starts_session_when_probe_says_down(roster, fleet, probes):
    down = dict(probes)
    down["ser6"] = probes["ser6"].__class__(**{**probes["ser6"].__dict__, "running": False})
    p = plan_set(roster, fleet, opts(down, targets=[SER6_BLOCKED]))
    start = next(s for s in p.steps if s.id.endswith("start") and s.kind.value == "shell")
    assert start.raw == ("sh", "-c", "systemctl --user start herdr.service") and start.mutating


def test_no_pane_layout_current_anywhere(roster, fleet, probes):
    p = plan_set(roster, fleet, opts(probes))
    assert not any("--current" in s.argv for s in p.steps)
    split = next(s for s in p.steps if s.verb == ("pane", "split") and "--pane" in s.raw)
    assert split.raw[split.raw.index("--pane") + 1] == "w4:p1"
