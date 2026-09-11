import pytest

from watchbill.plan import check_verbs_allowed
from watchbill.plan_secure import SecureOptions, plan_secure

API_WORKING = "rig2/default/api/1/p1"
SER6_BLOCKED = "ser6/default/sfl-site/1/p1"
SER6_GROK = "ser6/default/personal-config/1/p1"


def opts(**kw):
    base = dict(mode="park", cockpit_host="rig2", self_pane="w4:p1")
    base.update(kw)
    return SecureOptions(**base)


def verbs(plan):
    return [s.verb for s in plan.steps if s.verb]


def test_working_agent_refused_without_force(roster, fleet):
    p = plan_secure(roster, fleet, opts(targets=[API_WORKING], include_local=True))
    assert p.refused and p.refusals[0].override == "--force"
    p = plan_secure(roster, fleet, opts(targets=[API_WORKING], include_local=True, force=True))
    assert not p.refused
    assert any(s.raw[:2] == ("pane", "send-text") and s.raw[3] == "/exit" for s in p.steps)


def test_blocked_agent_is_never_typed_into(roster, fleet):
    p = plan_secure(roster, fleet, opts(targets=[SER6_BLOCKED], force=True))
    assert p.refused and p.refusals[0].override is None and "blocked" in p.refusals[0].reason
    assert not any(s.raw[:2] == ("pane", "send-text") for s in p.steps)


def test_self_pane_and_bridge_are_skipped(roster, fleet):
    p = plan_secure(roster, fleet, opts(mode="park", include_local=True, force=True))
    assert any("skip self pane" in n and "w4:p1" in n for n in p.notes)
    assert any("skip bridge" in n for n in p.notes)
    assert not any(s.slot_id == roster.by_human("rig2/default/bridge/1/p1").slot_id for s in p.steps)


def test_label_collision_across_hosts_needs_host(roster, fleet):
    p = plan_secure(roster, fleet, opts(targets=["personal-config"]))
    assert p.refused and p.refusals[0].override == "--host"
    p = plan_secure(roster, fleet, opts(targets=["personal-config"], host="ser6"))
    assert not p.refused and all(s.host in ("ser6", "rig2") for s in p.steps)
    assert all(s.host == "ser6" for s in p.steps if s.verb)


def test_cockpit_needs_include_local(roster, fleet):
    p = plan_secure(roster, fleet, opts(targets=["rig2/default/personal-config/1/p1"]))
    assert p.refused and p.refusals[0].override == "--include-local"
    p = plan_secure(roster, fleet, opts())  # fleet-wide: cockpit silently skipped with a note
    assert not any(s.host == "rig2" for s in p.steps if s.verb)
    assert any("cockpit" in n for n in p.notes)


def test_modes(roster, fleet):
    assert [s.kind.value for s in plan_secure(roster, fleet, opts(mode="detach")).steps] == ["snap"]
    fold = plan_secure(roster, fleet, opts(mode="fold", targets=[SER6_GROK]))
    assert ("workspace", "close") in verbs(fold)
    dismiss = plan_secure(roster, fleet, opts(mode="dismiss", targets=[SER6_GROK]))
    assert ("session", "stop") in verbs(dismiss) and ("server", "stop") not in verbs(dismiss)
    stop = next(s for s in dismiss.steps if s.verb == ("session", "stop"))
    assert stop.raw == ("session", "stop", "default")
    host = plan_secure(roster, fleet, opts(mode="host", targets=[SER6_GROK], force=True))
    assert ("session", "stop") in verbs(host)
    with pytest.raises(ValueError):
        plan_secure(roster, fleet, opts(mode="nuke"))


def test_watchers_are_interrupted_not_exited(roster, fleet):
    p = plan_secure(roster, fleet, opts(targets=["rig2/default/api/1/p2"], include_local=True))
    step = next(s for s in p.steps if s.verb == ("pane", "send-keys"))
    # verified on 0.8.2: `ctrl-c` is rejected with invalid_key, `C-c` is accepted
    assert step.raw[-1] == "C-c" and not step.unverified


def test_dry_run_schedules_nothing_mutating(roster, fleet):
    p = plan_secure(roster, fleet, opts(mode="dismiss", targets=[SER6_GROK]))
    assert p.mutating_steps() and not [s for s in p.scheduled() if s.mutating]
    assert check_verbs_allowed(p.steps) == []
    p.approved = True
    assert [s for s in p.scheduled() if s.mutating]


def test_ssh_argv_is_rendered_for_remote_hosts(roster, fleet):
    p = plan_secure(roster, fleet, opts(targets=[SER6_GROK]))
    step = next(s for s in p.steps if s.verb == ("pane", "send-text"))
    assert step.argv[:5] == ("ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5")
    assert "herdr --session default" in step.argv[-1] and "--remote" not in step.argv
