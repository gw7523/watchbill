"""Portability and scoping: Watchbill deployed inside another environment
(an exec_prefix such as a distrobox seat), per-host exclusions, and no
home-directory or Linux-only assumption where the target can be asked."""
import json

import pytest

from conftest import facts_from_fixture
from watchbill import collect, hosts
from watchbill.plan_relieve import RelieveOptions, keep_session_json_body, plan_relieve
from watchbill.plan_secure import SecureOptions, plan_secure
from watchbill.plan_set import SetOptions, plan_set
from watchbill.slots import SlotStore
from watchbill.transport import make_session

SEAT_HOSTS = """
[fleet]
name = "seats"
[[host]]
name = "sfl-rig2"
cockpit = true
transport = "local"
[[host]]
name = "sfl-ser6"
target = "ser6-lan"
exec_prefix = ["distrobox", "enter", "sfl", "--"]
"""


# -- exec_prefix: one Watchbill, commands run inside an environment on the target

def test_exec_prefix_wraps_every_mux_and_shell_command_remote_and_local():
    fleet = hosts.parse(SEAT_HOSTS, hostname="rig2")
    remote = make_session(fleet.host("sfl-ser6"), "default")
    assert remote.mux_argv("agent", "list")[-1] == "distrobox enter sfl -- herdr --session default agent list"
    assert remote.shell_argv(["sh", "-c", "true"])[-1] == "distrobox enter sfl -- sh -c true"
    local = make_session(hosts.Host(name="seat", transport="local", exec_prefix=["distrobox", "enter", "sfl", "--"]), "default")
    assert local.mux_argv("status", "server", "--json")[:6] == ["distrobox", "enter", "sfl", "--", "herdr", "--session"]
    assert make_session(hosts.Host(name="plain", transport="local"), "default").shell_argv(["true"]) == ["true"]


def test_exec_prefix_is_validated():
    with pytest.raises(ValueError):
        hosts.parse('[[host]]\nname = "x"\ntarget = "x"\nexec_prefix = "distrobox enter sfl"\n')
    with pytest.raises(ValueError):
        hosts.parse('[[host]]\nname = "x"\ntarget = "x"\ntransport = "herdr_remote"\nexec_prefix = ["a"]\n')


# -- nothing assumes where herdr keeps its files, or that setsid exists

def test_session_json_copy_asks_the_targets_herdr_instead_of_assuming_home():
    body = keep_session_json_body("default", "R1")
    assert "~/.config/herdr" not in body and "status server --json" in body and "dirname" in body
    assert body.rstrip().endswith("true")          # never fails the window if herdr cannot say


def test_default_start_has_a_fallback_where_setsid_is_missing():
    assert "command -v setsid" in hosts.DEFAULT_START and "nohup herdr --session {session} server" in hosts.DEFAULT_START


# -- exclusions: catalogued, never touched, and never killed as a side effect

@pytest.fixture
def excluded_roster(fleet_json, allowlist):
    facts = facts_from_fixture(fleet_json)
    ser6 = next(f for f in facts if f.host == "ser6")
    ser6.exclude = ["*npm run dev*"]              # stands in for the sfl distrobox pane
    return collect.build_roster("t", facts, slots=SlotStore(), allowlist=allowlist)


def test_roll_marks_the_match_and_the_schema_accepts_it(excluded_roster):
    ex = [o for o in excluded_roster.occupants if o.excluded]
    assert [o.human_id for o in ex] == ["ser6/default/sfl-site/1/p2"] and ex[0].excluded == "*npm run dev*"
    import jsonschema
    from pathlib import Path
    schema = json.loads((Path(__file__).parent.parent / "docs/roster.schema.json").read_text())
    jsonschema.Draft202012Validator(schema).validate(excluded_roster.to_dict())


def test_fleet_wide_park_skips_it_and_naming_it_is_refused(excluded_roster, fleet):
    p = plan_secure(excluded_roster, fleet, SecureOptions(host="ser6", cockpit_host="rig2", force=True))
    assert any("excluded" in n for n in p.notes)
    ex = excluded_roster.by_human("ser6/default/sfl-site/1/p2")
    assert not any(s.slot_id == ex.slot_id for s in p.steps)
    named = plan_secure(excluded_roster, fleet, SecureOptions(targets=[ex.human_id], cockpit_host="rig2", force=True))
    assert named.refused and "excluded" in named.refusals[0].reason and named.refusals[0].override is None


def test_a_session_stop_that_would_kill_it_is_refused(excluded_roster, fleet, probes):
    grok = "ser6/default/personal-config/1/p1"
    p = plan_secure(excluded_roster, fleet, SecureOptions(mode="dismiss", targets=[grok], cockpit_host="rig2"))
    assert p.refused and any("would kill excluded" in r.reason for r in p.refusals)
    excluded_roster.by_human("ser6/default/sfl-site/1/p1").agent_status = "idle"
    w = plan_relieve(excluded_roster, fleet, RelieveOptions(action="restart-harness", hosts=["ser6"], cockpit_host="rig2", probes=probes))
    assert w.refused and any("would kill excluded" in r.reason for r in w.refusals)
    assert not any(s.verb == ("session", "stop") for s in w.steps)


def test_a_window_that_keeps_the_session_up_still_runs_around_it(excluded_roster, fleet, probes):
    w = plan_relieve(excluded_roster, fleet, RelieveOptions(action="upgrade-agents", hosts=["ser6"],
                                                            action_options={"kinds": ["grok"]}, cockpit_host="rig2", probes=probes))
    assert not w.refused
    ex = excluded_roster.by_human("ser6/default/sfl-site/1/p2")
    assert not any(s.slot_id == ex.slot_id for s in w.steps)


def test_set_never_restores_it(excluded_roster, fleet, probes):
    p = plan_set(excluded_roster, fleet, SetOptions(host="ser6", cockpit_host="rig2", self_pane="w4:p1", probes=probes))
    ex = excluded_roster.by_human("ser6/default/sfl-site/1/p2")
    assert not any(s.slot_id == ex.slot_id and s.verb in (("pane", "run"), ("agent", "start")) for s in p.steps)
    assert any("excluded" in n and ex.human_id in n for n in p.notes)
