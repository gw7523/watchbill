"""After the session is back up, one occupant's failed restore must not strand the rest."""
from watchbill import exec as X
from watchbill import mux
from watchbill.journal import Journal
from watchbill.plan import Plan, Step, StepKind
from watchbill.plan_set import SetOptions, plan_set
from watchbill.transport.base import CmdResult


class OneBadResume:
    backend = mux.get("herdr")

    def __init__(self, bad_name):
        self.bad, self.started = bad_name, []

    def mux(self, *a, timeout=30.0):
        if a[:2] == ("agent", "start"):
            if a[2] == self.bad:
                return CmdResult(a, 1, "", '{"error":{"code":"agent_not_ready"}}')
            self.started.append(a[2])
        if a[:2] == ("pane", "read"):
            return CmdResult(a, 0, "No conversation found\n")
        return CmdResult(a, 0, "{}")

    def shell(self, argv, timeout=60.0):
        return CmdResult(tuple(argv), 0, "")


def restore_plan():
    p = Plan(verb="relieve", fleet="t", approved=True)
    for n in ("a", "b"):
        p.add(Step(id=f"{n}.start", kind=StepKind.HERDR, host="ser6", session="s", description="start", slot_id=f"SLOT{n}",
                   raw=("agent", "start", n, "--kind", "claude", "--pane", "w1:p1"), via="mux", mutating=True, phase="restore"))
        p.add(Step(id=f"{n}.idle", kind=StepKind.HERDR, host="ser6", session="s", description="wait", slot_id=f"SLOT{n}",
                   raw=("agent", "wait", n, "--until", "idle"), via="mux", phase="restore"))
    p.add(Step(id="done", kind=StepKind.JOURNAL, host="ser6", description="set-complete", mutating=True))
    return p


def test_a_failed_resume_costs_only_that_occupant(fleet, tmp_path):
    fake = OneBadResume("a")
    j = Journal(tmp_path / "j")
    res = X.Executor(fleet, j, run_id="r", session_factory=lambda h, s: fake, out=lambda *_: None).run(restore_plan())
    assert fake.started == ["b"]                                  # b still resumed
    assert len(res.failed) == 1 and res.skipped == 1              # only a's wait was skipped
    statuses = {e["status"] for e in j.entries()}
    assert "set-partial" in statuses and "set-complete" not in statuses   # --resume will come back for a


def test_a_failed_park_still_stops_the_host(fleet, tmp_path):
    """A session must never be stopped with an agent still running."""
    class NoExit(OneBadResume):
        def shell(self, argv, timeout=60.0):
            return CmdResult(tuple(argv), 1, "", "timed out")          # the exit poll never sees it leave
        def mux(self, *a, timeout=30.0):
            self.started.append(a[:2])
            return CmdResult(a, 0, "{}")
    fake = NoExit("x")
    p = Plan(verb="relieve", fleet="t", approved=True)
    p.add(Step(id="park.wait", kind=StepKind.WAIT, host="ser6", session="s", description="exit poll", slot_id="SLOTa",
               raw=("sh", "-c", "for i in $(seq 1 2); do false; done; exit 1"), via="shell"))
    p.add(Step(id="stop", kind=StepKind.HERDR, host="ser6", session="s", description="stop",
               raw=("session", "stop", "s"), via="mux", mutating=True, planned_stop=True))
    X.Executor(fleet, Journal(tmp_path / "k"), run_id="r", session_factory=lambda h, s: fake, out=lambda *_: None).run(p)
    assert ("session", "stop") not in fake.started


def test_planner_marks_only_per_occupant_steps_as_restore(roster, fleet, probes):
    p = plan_set(roster, fleet, SetOptions(targets=["ser6/default/sfl-site/1/p1"], cockpit_host="rig2", self_pane="w4:p1", probes=probes))
    restore = {s.id for s in p.steps if s.phase == "restore"}
    assert any(i.endswith(".start") and "claude" in i for i in restore)
    assert not any(s.verb in (("workspace", "create"), ("status", "server")) for s in p.steps if s.phase == "restore")
