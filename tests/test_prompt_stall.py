"""A stalled prompt (agent_prompt_stalled) is retried once after settling."""
from watchbill import exec as X
from watchbill import mux
from watchbill.journal import Journal
from watchbill.plan import Plan, Step, StepKind
from watchbill.transport.base import CmdResult


class Stalls:
    backend = mux.get("herdr")

    def __init__(self, stalls):
        self.stalls, self.prompts = stalls, 0

    def mux(self, *a, timeout=30.0):
        if a[:2] == ("agent", "get"):
            return CmdResult(a, 0, '{"id":"x","result":{"agent":{"agent_status":"idle"}}}')
        if a[:2] == ("agent", "prompt"):
            self.prompts += 1
            if self.prompts <= self.stalls:
                return CmdResult(a, 1, "", '{"error":{"code":"agent_prompt_stalled"}}')
        return CmdResult(a, 0, "{}")

    def shell(self, argv, timeout=60.0):
        return CmdResult(tuple(argv), 0, "")


def plan():
    p = Plan(verb="set", fleet="t", approved=True)
    p.add(Step(id="p", kind=StepKind.HERDR, host="ser6", session="s", description="prompt",
               raw=("agent", "prompt", "g", "hi", "--wait", "--until", "working", "--timeout", "10000"),
               via="mux", mutating=True, precondition="agent_status != blocked"))
    return p


def test_one_stall_is_retried_and_succeeds(fleet, tmp_path):
    fake, slept = Stalls(1), []
    res = X.Executor(fleet, Journal(tmp_path / "j"), run_id="1", session_factory=lambda h, s: fake,
                     out=lambda *_: None, sleep=slept.append).run(plan())
    assert res.code == 0 and fake.prompts == 2 and slept == [5.0]


def test_a_second_stall_fails_instead_of_looping(fleet, tmp_path):
    fake = Stalls(5)
    res = X.Executor(fleet, Journal(tmp_path / "j"), run_id="2", session_factory=lambda h, s: fake,
                     out=lambda *_: None, sleep=lambda s: None).run(plan())
    assert res.failed and "agent_prompt_stalled" in res.failed[0] and fake.prompts == 2
