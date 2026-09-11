import json

from watchbill import exec as X
from watchbill.exitcodes import OK, REFUSED, TRANSPORT
from watchbill.journal import Journal
from watchbill.plan import Plan, Step, StepKind
from watchbill.plan_relieve import RelieveOptions, plan_relieve
from watchbill.plan_secure import SecureOptions, plan_secure
from watchbill.plan_set import SetOptions, plan_set

SER6_GROK = "ser6/default/personal-config/1/p1"
SER6_CLAUDE = "ser6/default/sfl-site/1/p1"


def journal(tmp_path):
    return Journal(tmp_path / "journal.jsonl")


def test_dry_run_never_reaches_a_mutating_verb(roster, fleet, probes, fake_sessions, tmp_path):
    plans = [
        plan_secure(roster, fleet, SecureOptions(mode="dismiss", targets=[SER6_GROK], cockpit_host="rig2", self_pane="w4:p1")),
        plan_set(roster, fleet, SetOptions(targets=[SER6_CLAUDE], cockpit_host="rig2", self_pane="w4:p1", probes=probes)),
        plan_relieve(roster, fleet, RelieveOptions(action="upgrade-agents", hosts=["ser6"], action_options={"kinds": ["grok"]},
                                                   cockpit_host="rig2", self_pane="w4:p1", probes=probes)),
    ]
    for plan in plans:
        assert not plan.approved
        for bad in (("session", "stop"), ("pane", "close"), ("agent", "prompt"), ("agent", "start"), ("pane", "send-text")):
            assert bad not in [s.verb for s in plan.scheduled()]
        j = journal(tmp_path)
        res = X.Executor(fleet, j, run_id="r1", session_factory=fake_sessions).run(plan)   # FakeSession raises on mutation
        assert res.code == OK and res.dry == len(plan.mutating_steps())
        assert {e["status"] for e in j.entries()} <= {"dry-run", "ok", "host-start", "set-complete"}
        assert not any(is_mut for (_h, is_mut) in [(c, c[1:3] in {("session", "stop"), ("pane", "close")})
                                                   for fs in fake_sessions.made.values() for c in fs.calls])


def test_approved_set_resolves_placeholders(roster, fleet, probes, fleet_json, tmp_path):
    from conftest import FakeSession
    made = {}

    def factory(host, session):
        return made.setdefault((host.name, session), FakeSession(host, session, fleet_json, allow_mutation=True))
    plan = plan_set(roster, fleet, SetOptions(targets=[SER6_CLAUDE], cockpit_host="rig2", self_pane="w4:p1", probes=probes, approved=True))
    res = X.Executor(fleet, journal(tmp_path), run_id="r2", session_factory=factory).run(plan)
    assert res.code == OK and not res.failed
    slot = roster.by_human(SER6_CLAUDE).slot_id
    assert slot in res.pane_map and res.pane_map[slot].startswith("w1")
    start = next(c for c in made[("ser6", "default")].calls if c[1:3] == ("agent", "start"))
    assert res.pane_map[slot] in start and not any(t.startswith("{pane:") for t in start)
    # viewport was split off the cockpit pane and attached over ssh -tt (#2064)
    rig = made[("rig2", "default")].calls
    assert any(c[1:3] == ("pane", "split") and "w4:p1" in c for c in rig)
    assert any(c[1:3] == ("pane", "run") and "ssh" in c and "-tt" in c for c in rig)


def test_server_stop_refused_without_flag(fleet, tmp_path):
    plan = Plan(verb="secure", fleet="t", approved=True)
    plan.add(Step(id="x", kind=StepKind.HERDR, host="ser6", session="default", description="bad",
                  argv=("herdr", "--session", "default", "server", "stop"), raw=("server", "stop"), mutating=True))
    j = journal(tmp_path)
    assert X.Executor(fleet, j, run_id="r3").run(plan).code == REFUSED
    assert j.entries()[0]["status"] == "refused"
    assert X.Executor(fleet, j, run_id="r3", force_server_stop=True, session_factory=lambda h, s: _Boom()).run(plan).code == TRANSPORT


class _Boom:
    def herdr(self, *a, **k):
        raise RuntimeError("transport exploded")

    def shell(self, *a, **k):
        raise RuntimeError("transport exploded")


def test_refused_plan_is_not_executed(fleet, tmp_path):
    from watchbill.plan import Refusal
    plan = Plan(verb="secure", fleet="t", approved=True, refusals=[Refusal("nope")])
    assert X.Executor(fleet, journal(tmp_path), run_id="r4").run(plan).code == REFUSED


def test_agent_exit_after_prompt_is_a_failure(fleet, fleet_json, tmp_path):
    """Pitfall 13 (#3632): an agent that vanishes after `agent prompt` is a relieve failure."""
    from conftest import FakeSession
    fs = FakeSession(fleet.host("ser6"), "default", fleet_json, allow_mutation=True, agent_gone={"grok-x"})
    plan = Plan(verb="set", fleet="t", approved=True)
    plan.add(Step(id="p", kind=StepKind.HERDR, host="ser6", session="default", description="prompt",
                  argv=("herdr", "--session", "default", "agent", "prompt", "grok-x", "go"),
                  raw=("agent", "prompt", "grok-x", "go"), mutating=True, via="herdr"))
    res = X.Executor(fleet, journal(tmp_path), run_id="r5", session_factory=lambda h, s: fs).run(plan)
    assert res.code == TRANSPORT and "vanished" in res.failed[0]


def test_journal_marks_set_complete_for_resume(fleet, tmp_path):
    plan = Plan(verb="relieve", fleet="t", approved=True)
    plan.add(Step(id="ser6.begin", kind=StepKind.JOURNAL, host="ser6", description="host-start x", mutating=True))
    plan.add(Step(id="ser6.done", kind=StepKind.JOURNAL, host="ser6", description="set-complete", mutating=True))
    j = journal(tmp_path)
    X.Executor(fleet, j, run_id="r6").run(plan)
    assert j.hosts_marked("r6") == {"ser6"} and j.last_run_id("relieve") == "r6"
    assert json.loads((tmp_path / "journal.jsonl").read_text().splitlines()[0])["run_id"] == "r6"
