import re

from conftest import facts_from_fixture
from watchbill import collect
from watchbill.slots import SlotStore, new_ulid

ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


def test_ulid_shape_and_time_order():
    a = new_ulid(now_ms=1_000, randomness=b"\x00" * 10)
    b = new_ulid(now_ms=2_000, randomness=b"\x00" * 10)
    assert ULID.match(a) and ULID.match(b) and a < b
    assert len({new_ulid() for _ in range(50)}) == 50


def test_assign_same_human_id_is_stable(tmp_path):
    s = SlotStore()
    a = s.assign(human_id="h/s/ws/1/p1", host="h", agent_session="x")
    b = s.assign(human_id="h/s/ws/1/p1", host="h", agent_session="x")
    assert a == b
    s.save(tmp_path / "slots.json")
    assert SlotStore.load(tmp_path / "slots.json").assign(human_id="h/s/ws/1/p1", host="h") == a


def test_slot_follows_agent_session_when_relabelled():
    s = SlotStore()
    a = s.assign(human_id="h/s/old/1/p1", host="h", agent_session="conv-1")
    b = s.assign(human_id="h/s/new/1/p1", host="h", agent_session="conv-1")
    assert a == b
    assert s.slots[a].human_id == "h/s/new/1/p1"


def test_slot_ids_survive_pane_id_change(fleet_json, tmp_path, allowlist):
    """Pitfall 5: pane ids are generation-scoped; slot_id must not be."""
    store = SlotStore()
    r1 = collect.build_roster("t", facts_from_fixture(fleet_json), slots=store, allowlist=allowlist)
    r2 = collect.build_roster("t", facts_from_fixture(fleet_json, pane_id_offset=10), slots=store, allowlist=allowlist)
    by_human1 = {o.human_id: o for o in r1.occupants}
    by_human2 = {o.human_id: o for o in r2.occupants}
    assert set(by_human1) == set(by_human2)
    for hid, o1 in by_human1.items():
        o2 = by_human2[hid]
        assert o1.slot_id == o2.slot_id
        assert o1.live_ids.pane_id != o2.live_ids.pane_id
