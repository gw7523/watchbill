import json

from watchbill import roster as R


def occ(i):
    return R.Occupant(slot_id=f"01ARZ3NDEKTSV4RRFFQ69G5F{i:02d}", human_id=f"h/s/w{i}/1/p1", host="h", session="s",
                      workspace_label=f"w{i}", tab_label="1", pane_label="p1", role="agent", kind="claude")


def mk(n):
    return R.Roster(fleet="t", taken_at=f"2026-09-11T00:00:{n:02d}+00:00", reason="heartbeat", occupants=[occ(i) for i in range(n)])


def test_guard_matrix():
    g = R.occupant_guard
    assert g(0, 0).ok and g(0, 5).ok                  # first roster
    assert not g(6, 1).ok                              # #3415 empty session.json after reboot
    assert not g(6, 3).ok                              # dropped by exactly the ratio
    assert g(6, 4).ok
    assert not g(3, 1).ok                              # below min_occupants
    assert g(1, 1).ok                                  # a one-occupant fleet is allowed to stay one
    assert g(6, 1, force=True).ok
    assert g(6, 1, R.GuardConfig(drop_ratio=0.9, min_occupants=1)).ok


def test_write_keeps_current_on_refusal(tmp_path):
    d = tmp_path / "rosters" / "t"
    p6, res = R.write(mk(6), d)
    assert res.ok and (d / "current.json").resolve() == p6.resolve()
    p1, res = R.write(mk(1), d)
    assert not res.ok and "6→1" in res.reason
    assert p1.exists()                                 # timestamped file kept
    assert (d / "current.json").resolve() == p6.resolve()
    p1b, res = R.write(mk(1), d, force=True)
    assert res.ok and (d / "current.json").resolve() == p1b.resolve()
    assert len(json.loads((d / "current.json").read_text())["occupants"]) == 1


def test_merge_carries_pins_forward():
    prev, cur = mk(2), mk(2)
    prev.occupants[0].resume_prompt = R.ResumePrompt("pinned", "keep going")
    prev.occupants[1].allow_relaunch = True
    out = R.merge(cur, prev)
    assert out.occupants[0].resume_prompt.text == "keep going"
    assert out.occupants[1].allow_relaunch
