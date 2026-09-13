"""`ignore`: never managed, but not protected (owner, 2026-09-13: stopping ser6
sessions is fine; the SFL distrobox is still not to be used)."""
from conftest import facts_from_fixture
from watchbill import collect, hosts
from watchbill.plan_relieve import RelieveOptions, plan_relieve
from watchbill.plan_secure import SecureOptions, plan_secure
from watchbill.plan_set import SetOptions, plan_set
from watchbill.slots import SlotStore


def roster_with(fleet_json, allowlist, **globs):
    facts = facts_from_fixture(fleet_json)
    ser6 = next(f for f in facts if f.host == "ser6")
    ser6.exclude = globs.get("exclude", [])
    ser6.ignore = globs.get("ignore", [])
    return collect.build_roster("t", facts, slots=SlotStore(), allowlist=allowlist)


def test_parsed_and_marked(fleet_json, allowlist):
    assert hosts.parse('[[host]]\nname = "x"\ntarget = "x"\nignore = ["*a*"]\n').hosts[0].ignore == ["*a*"]
    r = roster_with(fleet_json, allowlist, ignore=["*npm run dev*"])
    o = r.by_human("ser6/default/sfl-site/1/p2")
    assert o.ignored == "*npm run dev*" and o.excluded is None
    both = roster_with(fleet_json, allowlist, ignore=["*npm run dev*"], exclude=["*npm run dev*"])
    assert both.by_human("ser6/default/sfl-site/1/p2").excluded and not both.by_human("ser6/default/sfl-site/1/p2").ignored


def test_a_stop_proceeds_and_names_what_it_ends(fleet_json, allowlist, fleet, probes):
    r = roster_with(fleet_json, allowlist, ignore=["*npm run dev*"])
    r.by_human("ser6/default/sfl-site/1/p1").agent_status = "idle"
    w = plan_relieve(r, fleet, RelieveOptions(action="restart-harness", hosts=["ser6"], cockpit_host="rig2", probes=probes))
    assert not w.refused
    assert any(s.verb == ("session", "stop") for s in w.steps)
    assert any("ENDS ignored ser6/default/sfl-site/1/p2" in n for n in w.notes)
    ig = r.by_human("ser6/default/sfl-site/1/p2")
    assert not any(s.slot_id == ig.slot_id for s in w.steps)           # never parked, never restored


def test_still_never_parked_or_restored(fleet_json, allowlist, fleet, probes):
    r = roster_with(fleet_json, allowlist, ignore=["*npm run dev*"])
    ig = r.by_human("ser6/default/sfl-site/1/p2")
    p = plan_secure(r, fleet, SecureOptions(targets=[ig.human_id], cockpit_host="rig2"))
    assert p.refused and "ignored" in p.refusals[0].reason
    d = plan_secure(r, fleet, SecureOptions(mode="dismiss", targets=["ser6/default/personal-config/1/p1"], cockpit_host="rig2"))
    assert not d.refused and any("ENDS ignored" in n for n in d.notes)
    s = plan_set(r, fleet, SetOptions(host="ser6", cockpit_host="rig2", self_pane="w4:p1", probes=probes))
    assert not any(st.slot_id == ig.slot_id and st.verb in (("pane", "run"), ("agent", "start")) for st in s.steps)
