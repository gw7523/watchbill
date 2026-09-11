import pytest

from watchbill import actions
from watchbill.actions import ActionContext, ActionUnavailable


def ctx(probes, **opts):
    return ActionContext(options=opts, probes=probes)


def test_matrix_from_contract():
    t = actions.blast_radius_table()
    assert t["upgrade-agents"].needs_session_stop is False
    assert t["upgrade-agents"].park_roles == {"agent"}
    assert t["upgrade-herdr"].needs_session_stop is True
    assert t["restart-herdr"].needs_session_stop is True
    assert t["restart-harness"].needs_session_stop is True
    assert t["omarchy-update"].needs_session_stop is True
    assert t["install-plugin"].needs_session_stop is False and t["install-plugin"].park_roles == frozenset()
    assert t["custom"].needs_session_stop is False and t["custom"].park_roles == frozenset()
    assert not any(b.allow_reboot for b in t.values())


def test_install_plugin_with_startup_hooks_bounces(probes):
    a = actions.get("install-plugin", ctx(probes, plugin="o/r", startup_hooks=True))
    b = a.blast_radius()
    assert b.needs_session_stop and b.park_roles == {"agent"}
    a2 = actions.get("install-plugin", ctx(probes, plugin="o/r"))
    assert not a2.blast_radius().needs_session_stop  # enable ≠ hook fired; no hooks → no bounce


def test_custom_declares_everything(probes):
    a = actions.get("custom", ctx(probes, cmd="sudo systemctl restart tailscaled", park="agent,watcher", session_stop=True, may_reboot=True))
    b = a.blast_radius()
    assert b.park_roles == {"agent", "watcher"} and b.needs_session_stop and b.allow_reboot


def test_pacman_host_never_gets_herdr_update(fleet, probes):
    ser6 = fleet.host("ser6")
    option_sets = {
        "upgrade-herdr": {}, "restart-herdr": {}, "omarchy-update": {}, "restart-harness": {},
        "upgrade-agents": {"kinds": ["claude"]}, "install-plugin": {"plugin": "o/r"}, "custom": {"cmd": "true"},
    }
    for name, opts in option_sets.items():
        a = actions.get(name, ctx(probes, **opts))
        try:
            cmds = a.commands(ser6, probes["ser6"])
        except ActionUnavailable:
            assert name == "upgrade-herdr"
            continue
        for c in cmds:
            assert not (c.argv[0].endswith("herdr") and "update" in c.argv[1:]), (name, c.argv)
    a = actions.get("upgrade-herdr", ctx(probes, allow_partial_pacman=True))
    cmds = a.commands(ser6, probes["ser6"])
    assert cmds[0].argv[:3] == ("sudo", "pacman", "-S")
    assert all("update" not in c.argv for c in cmds)


def test_official_flavor_uses_herdr_update(fleet, probes):
    vps = fleet.host("vps")
    assert actions.get("upgrade-herdr", ctx(probes)).commands(vps, probes["vps"])[0].argv == ("herdr", "update")
    assert actions.get("upgrade-herdr", ctx(probes, mode="live")).commands(vps, probes["vps"])[0].argv == ("herdr", "update", "--handoff")


def test_upgrade_agents_mise_flavor_and_integration_refresh(fleet, probes):
    cmds = actions.get("upgrade-agents", ctx(probes)).commands(fleet.host("rig2"), probes["rig2"])
    argvs = [c.argv for c in cmds]
    assert ("mise", "upgrade", "claude") in argvs and ("mise", "upgrade", "codex") in argvs
    assert ("herdr", "integration", "install", "claude") in argvs
    assert all(c.via == "mux" for c in cmds if "integration" in c.argv)
    b = actions.get("upgrade-agents", ctx(probes, kinds=["grok"])).blast_radius()
    assert b.park_kinds == {"grok"} and not b.needs_session_stop


def test_omarchy_update_only_on_pacman_and_unattended(fleet, probes):
    a = actions.get("omarchy-update", ctx(probes))
    assert a.commands(fleet.host("ser6"), probes["ser6"])[0].argv == ("omarchy-update", "-y")
    with pytest.raises(ActionUnavailable):
        a.commands(fleet.host("vps"), probes["vps"])


def test_unknown_action_is_refused():
    with pytest.raises(ActionUnavailable):
        actions.get("upgrade-claude")
