import pytest

from watchbill.exitcodes import RefusedPlan
from watchbill.plan import check_verbs_allowed
from watchbill.plan_relieve import RelieveOptions, plan_relieve


def opts(probes, action, **kw):
    base = dict(action=action, cockpit_host="rig2", self_pane="w4:p1", probes=probes)
    base.update(kw)
    return RelieveOptions(**base)


def verbs(plan):
    return [s.verb for s in plan.steps if s.verb]


def test_live_mode_rejected_when_handoff_unsupported(roster, fleet, probes):
    with pytest.raises(RefusedPlan, match="live handoff unsupported"):
        plan_relieve(roster, fleet, opts(probes, "upgrade-herdr", mode="live", hosts=["ser6"]))
    with pytest.raises(RefusedPlan):   # the flag alone (true on pacman) is not enough
        assert probes["ser6"].live_handoff_flag
        plan_relieve(roster, fleet, opts(probes, "restart-herdr", mode="live"))


def test_live_mode_allowed_on_official_install(roster, fleet, probes):
    p = plan_relieve(roster, fleet, opts(probes, "upgrade-herdr", mode="live", hosts=["vps"]))
    assert not p.refused
    assert any(s.raw == ("herdr", "update", "--handoff") for s in p.steps)


def test_upgrade_agents_does_not_stop_the_session(roster, fleet, probes):
    p = plan_relieve(roster, fleet, opts(probes, "upgrade-agents", hosts=["ser6"], action_options={"kinds": ["grok"]}))
    assert not p.refused, p.refusals
    v = verbs(p)
    assert ("session", "stop") not in v and ("server", "stop") not in v
    assert ("pane", "send-text") in v                       # grok parked
    assert ("agent", "start") in v                          # and resumed on the new binary
    assert any(s.raw == ("mise", "upgrade", "npm:@xai-official/grok") or s.raw[:2] == ("npm", "install") for s in p.steps)
    assert any(s.raw == ("integration", "install", "grok") for s in p.steps)
    assert not any(s.slot_id == roster.by_human("ser6/default/sfl-site/1/p1").slot_id for s in p.steps)  # claude not in kinds


def test_upgrade_herdr_cold_on_official_host_stops_and_restarts(roster, fleet, probes):
    p = plan_relieve(roster, fleet, opts(probes, "upgrade-herdr", hosts=["vps"], expected_version="0.8.3"))
    assert not p.refused
    ids = [s.id for s in p.steps]
    order = [ids.index("snap"), ids.index("guard"), ids.index("vps.keep.default"), ids.index("vps.stop1"),
             ids.index("vps.act1"), ids.index("vps.verify1"), ids.index("vps.expect"), ids.index("vps.set1.start"), ids.index("vps.done")]
    assert order == sorted(order)
    assert next(s for s in p.steps if s.id == "vps.stop1").raw == ("session", "stop", "default")
    assert ("server", "stop") not in verbs(p)


def test_pacman_host_upgrade_herdr_is_refused_and_never_herdr_update(roster, fleet, probes):
    p = plan_relieve(roster, fleet, opts(probes, "upgrade-herdr", hosts=["ser6"]))
    assert p.refused and "pacman" in p.refusals[0].reason
    assert not any(s.raw[:1] == ("update",) or s.raw[:2] == ("herdr", "update") for s in p.steps)


def test_omarchy_update_window_on_pacman_host(roster, fleet, probes):
    p = plan_relieve(roster, fleet, opts(probes, "omarchy-update", hosts=["ser6"], force=True))
    # ser6 has a blocked claude → park refusal; the window must not proceed around it
    assert p.refused and "blocked" in p.refusals[0].reason
    roster.by_human("ser6/default/sfl-site/1/p1").agent_status = "idle"
    p = plan_relieve(roster, fleet, opts(probes, "omarchy-update", hosts=["ser6"]))
    assert not p.refused
    assert any(s.raw == ("omarchy-update", "-y") for s in p.steps)
    assert ("session", "stop") in verbs(p)


def test_resume_skips_completed_hosts_and_cockpit_needs_include_local(roster, fleet, probes):
    p = plan_relieve(roster, fleet, opts(probes, "restart-harness", resume_done={"ser6"}))
    assert any("already set-complete" in n for n in p.notes)
    assert any("skip cockpit host rig2" in n for n in p.notes)
    assert not any(s.host == "ser6" for s in p.steps if s.verb)
    assert not any(s.slot_id and roster.by_slot(s.slot_id).host in ("ser6", "rig2") for s in p.steps)  # only vps is worked
    assert any(s.host == "vps" for s in p.steps if s.verb)


def test_reboot_gates(roster, fleet, probes):
    custom = {"cmd": "sudo reboot", "may_reboot": True}
    p = plan_relieve(roster, fleet, opts(probes, "custom", hosts=["vps"], action_options=custom))
    assert p.refused and p.refusals[0].override == "--allow-reboot"
    p = plan_relieve(roster, fleet, opts(probes, "custom", hosts=["vps"], action_options=custom, allow_reboot=True))
    assert not p.refused
    p = plan_relieve(roster, fleet, opts(probes, "custom", hosts=["rig2"], action_options=custom, allow_reboot=True, include_local=True))
    assert p.refused and "never reboot the cockpit" in p.refusals[0].reason


def test_install_plugin_with_and_without_startup_hooks(roster, fleet, probes):
    p = plan_relieve(roster, fleet, opts(probes, "install-plugin", hosts=["vps"], action_options={"plugin": "o/r"}))
    assert ("session", "stop") not in verbs(p) and ("plugin", "install") in verbs(p)
    p = plan_relieve(roster, fleet, opts(probes, "install-plugin", hosts=["vps"], action_options={"plugin": "o/r", "startup_hooks": True}))
    assert ("session", "stop") in verbs(p)


def test_never_emits_machine_or_server_stop(roster, fleet, probes):
    for action, ao in (("upgrade-herdr", {}), ("restart-harness", {}), ("upgrade-agents", {"kinds": ["claude"]})):
        p = plan_relieve(roster, fleet, opts(probes, action, hosts=["vps"], action_options=ao))
        assert check_verbs_allowed(p.steps) == []
        assert not any("machine" in s.argv for s in p.steps)
        assert not any("--current" in s.argv for s in p.steps)
