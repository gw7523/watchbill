"""One test per numbered Herdr 0.8.2 pitfall from the build contract.
Pitfalls that need a live box are asserted structurally (the plan never
emits the bad thing) and listed as UNVERIFIED in docs/architecture.md."""
import json

import pytest

from conftest import facts_from_fixture
from watchbill import collect, doctor, hosts
from watchbill.classify import classify
from watchbill.plan_secure import SecureOptions, plan_secure
from watchbill.plan_set import SetOptions, plan_set
from watchbill.roster import occupant_guard
from watchbill.slots import SlotStore
from watchbill.transport import make_session

pitfall = pytest.mark.pitfall


@pitfall
def test_01_default_transport_is_ssh_cli_not_remote(fleet):
    h = fleet.host("ser6")
    assert h.transport == "ssh_cli"
    argv = make_session(h, "default").herdr_argv("agent", "list")
    assert argv[0] == "ssh" and "BatchMode=yes" in argv and "--remote" not in " ".join(argv)
    assert argv[-1] == "herdr --session default agent list"


@pitfall
def test_02_never_pane_layout_current(roster, fleet, probes):
    for plan in (plan_secure(roster, fleet, SecureOptions(mode="fold", cockpit_host="rig2", force=True)),
                 plan_set(roster, fleet, SetOptions(cockpit_host="rig2", self_pane="w4:p1", probes=probes))):
        assert not any("--current" in s.argv for s in plan.steps)


@pitfall
def test_03_argv_comes_from_process_info_not_layout(fleet_json, allowlist):
    facts = facts_from_fixture(fleet_json)
    lay = facts[0].sessions[0].snapshot["layouts"][1]
    lay["panes"][1]["command"] = "vim lies.txt"          # a layout.export-style command field
    r = collect.build_roster("t", facts, slots=SlotStore(), allowlist=allowlist)
    o = r.by_human("rig2/default/api/1/p2")
    assert o.role == "watcher" and o.argv[0] == "watchexec"


@pitfall
def test_04_prefer_foreground_cwd(roster):
    o = roster.by_human("rig2/default/bridge/1/p1")
    assert o.cwd.endswith("/Work") and o.foreground_cwd.endswith("/Work/sfl") and o.effective_cwd == o.foreground_cwd


@pitfall
def test_05_pane_ids_are_hints_not_keys(roster, fleet, probes):
    p = plan_set(roster, fleet, SetOptions(targets=["ser6/default/sfl-site/1/p1"], cockpit_host="rig2", self_pane="w4:p1", probes=probes))
    for s in p.steps:
        if s.verb == ("agent", "start"):
            assert s.slot_id and s.human_id and not any(t.startswith("w") and ":p" in t for t in s.raw)


@pitfall
def test_06_attach_client_before_resume(roster, fleet, probes):
    p = plan_set(roster, fleet, SetOptions(targets=["ser6/default/sfl-site/1/p1"], cockpit_host="rig2", self_pane="w4:p1", probes=probes))
    ids = [s.id for s in p.steps]
    assert ids.index("set1.att2") < ids.index(next(i for i in ids if i.endswith(".start") and "att" not in i))


@pitfall
def test_07_unref_agent_session(roster):
    o = roster.by_human("rig2/default/api#3/1/p1")
    assert o.kind == "codex" and o.agent_session is None and o.resume_argv is None and o.unref


@pitfall
def test_08_occupant_guard_after_reboot():
    assert not occupant_guard(6, 1).ok and not occupant_guard(6, 0).ok


@pitfall
def test_09_skip_self_pane(roster, fleet):
    p = plan_secure(roster, fleet, SecureOptions(mode="park", cockpit_host="rig2", self_pane="w4:p1", include_local=True, force=True))
    self_slot = roster.by_human("rig2/default/Work/1/p1").slot_id
    assert not any(s.slot_id == self_slot for s in p.steps) and any("skip self pane" in n for n in p.notes)


@pitfall
def test_10_nested_herdr_is_bridge():
    pi = {"foreground_processes": [{"argv": ["herdr", "--session", "default"], "cmdline": "", "cwd": "/", "name": "herdr", "pid": 1}]}
    assert classify({"pane_id": "w1:p1"}, pi).role == "bridge"


@pitfall
def test_11_label_collision_fails_closed(roster, fleet):
    assert plan_secure(roster, fleet, SecureOptions(targets=["personal-config"], cockpit_host="rig2")).refused


@pitfall
def test_12_blocked_agent_never_prompted_or_exited(roster, fleet, probes):
    sec = plan_secure(roster, fleet, SecureOptions(targets=["ser6/default/sfl-site/1/p1"], cockpit_host="rig2", force=True))
    assert sec.refused
    st = plan_set(roster, fleet, SetOptions(targets=["ser6/default/sfl-site/1/p1"], cockpit_host="rig2", self_pane="w4:p1", probes=probes))
    for s in st.steps:
        if s.verb == ("agent", "prompt"):
            assert s.precondition == "agent_status != blocked"
            prev = st.steps[st.steps.index(s) - 1]
            assert prev.verb == ("agent", "wait") and "idle" in prev.raw


@pitfall
def test_13_codex_update_dialog_is_not_success():
    # exec-level behaviour is in test_exec; here: doctor never treats a pacman
    # `live_handoff: true` as supported, the sibling misclassification trap.
    p = doctor.assess("ser6", {"running": True, "version": "0.8.2", "protocol": 20, "capabilities": {"live_handoff": True}},
                      "/usr/bin/herdr", pacman_owned=True)
    assert p.live_handoff_flag and not p.handoff_supported and p.flavor == "pacman"
    o = doctor.assess("vps", {"running": True, "version": "0.8.2", "protocol": 20, "capabilities": {"live_handoff": True}},
                      "/home/u/.local/bin/herdr", home="/home/u")
    assert o.handoff_supported and o.flavor == "official"


def test_duplicate_pane_names_in_one_tab_get_distinct_slots(fleet_json, allowlist):
    facts = facts_from_fixture(fleet_json)
    snap = facts[0].sessions[0].snapshot
    for p in snap["panes"]:
        if p["workspace_id"] == "w2":
            p["name"] = "claude"          # both panes of api/1 named claude
    r = collect.build_roster("t", facts, slots=SlotStore(), allowlist=allowlist)
    ids = sorted(o.human_id for o in r.occupants if o.workspace_label == "api")
    assert ids == ["rig2/default/api/1/claude", "rig2/default/api/1/claude#2"]
    assert len({o.slot_id for o in r.occupants}) == len(r.occupants)


def test_herdr_remote_needs_doctor_match():
    from watchbill.exitcodes import TransportError
    h = hosts.Host(name="x", target="x.example", transport="herdr_remote")
    with pytest.raises(TransportError):
        make_session(h, "default")
    h.remote_verified = True
    assert make_session(h, "default").herdr_argv("agent", "list")[:3] == ["herdr", "--remote", "x.example"]


def test_hosts_toml_rejects_remote_without_target():
    with pytest.raises(ValueError):
        hosts.parse('[[host]]\nname = "x"\ntransport = "ssh_cli"\n')
    f = hosts.parse('[[host]]\nname = "rig2"\n', hostname="rig2")
    assert f.hosts[0].cockpit and f.hosts[0].transport == "local"
