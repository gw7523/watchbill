import json
from pathlib import Path

import pytest

from conftest import FIXTURES, facts_from_fixture
from watchbill import cli, collect, paths

PROBES = str(FIXTURES / "probes.json")


@pytest.fixture
def fixture_collect(fleet_json, monkeypatch):
    facts = {f.host: f for f in facts_from_fixture(fleet_json)}

    def gather_host(host, *, excerpts=False):
        return facts.get(host.name) or collect.HostFacts(host=host.name, reachable=False, error="not in fixture")
    monkeypatch.setattr(collect, "gather_host", gather_host)
    return facts


@pytest.mark.parametrize("argv", [["--help"], ["roll", "--help"], ["snap", "--help"], ["secure", "--help"], ["set", "--help"],
                                  ["relieve", "--help"], ["overhaul", "--help"], ["status", "--help"], ["doctor", "--help"]])
def test_help_surface(argv, capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(argv)
    assert e.value.code == 0


def test_verbs_match_contract():
    p = cli.build_parser()
    verbs = set(p._subparsers._group_actions[0].choices)
    assert {"roll", "snap", "secure", "set", "relieve", "overhaul", "status", "doctor"} <= verbs
    assert "upgrade-claude" not in verbs and "upgrade-herdr" not in verbs
    assert cli.main([]) == 2


def test_roll_and_snap_and_guard(xdg, fixture_collect, capsys):
    assert cli.main(["roll"]) == 1                       # vps is not in the fixture → partial
    out = capsys.readouterr().out
    assert "occupants 11" in out and "bridge" in out and "not collected: vps" in out
    assert cli.main(["snap", "--host", "ser6"]) == 0     # only reachable hosts → ok
    assert paths.current_roster("test").exists()
    capsys.readouterr()
    assert cli.main(["roll", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["schema"] == 1


def test_secure_dry_run_and_refusals(xdg, fixture_collect, capsys):
    assert cli.main(["secure", "park", "--host", "ser6", "--json"]) == 3      # ser6 claude is blocked
    plan = json.loads(capsys.readouterr().out)
    assert plan["refusals"] and not plan["approved"]
    assert cli.main(["secure", "park", "rig2/default/api/1/p1", "--include-local"]) == 3   # working, no --force
    assert "[--force]" in capsys.readouterr().out
    assert cli.main(["secure", "park", "ser6/default/personal-config/1/p1"]) == 0
    out = capsys.readouterr().out
    assert "DRY-RUN" in out and "/exit" in out


def test_secure_yes_snaps_first_then_reports_a_transport_failure(xdg, fixture_collect, capsys):
    """--yes writes the pre-secure roster, then runs. `ser6.example` is an
    RFC-2606 reserved name that cannot resolve, so the ssh transport fails
    cleanly: exit 4, the error journaled, later steps on that host skipped,
    and no exception escapes."""
    assert cli.main(["secure", "park", "ser6/default/personal-config/1/p1", "--yes"]) == 4
    out = capsys.readouterr().out
    assert "pre-secure" in out and "FAIL ser6" in out
    rosters = list(paths.rosters_dir("test").glob("*-pre-secure.json"))
    assert len(rosters) == 1 and paths.current_roster("test").exists()
    entries = json.loads("[" + ",".join(paths.journal_file().read_text().splitlines()) + "]")
    fails = [e for e in entries if e["status"] == "fail"]
    assert fails and fails[0]["host"] == "ser6"
    assert any(e["status"] == "skipped" for e in entries)   # the rest of that host is abandoned


def test_snap_refuses_when_every_host_is_down(xdg, fixture_collect, capsys):
    assert cli.main(["snap", "--host", "vps"]) == 1
    assert "no host reachable" in capsys.readouterr().err
    assert not paths.current_roster("test").exists()


def test_set_needs_roster_then_plans(xdg, fixture_collect, capsys):
    assert cli.main(["set"]) == 2
    assert cli.main(["snap", "--host", "ser6"]) == 0
    assert cli.main(["set", "ser6/default/sfl-site/1/p1", "--probes-json", PROBES]) == 0
    out = capsys.readouterr().out
    # herdr prepends the executable itself: only the arguments follow `--`
    assert "-- --resume b1b1b1b1" in out and "-- claude" not in out and "#2064" in out


def test_relieve_live_refused_and_cold_plans(xdg, fixture_collect, capsys):
    assert cli.main(["relieve", "upgrade-herdr", "--mode", "live", "--probes-json", PROBES]) == 3
    assert "live handoff unsupported" in capsys.readouterr().err
    assert cli.main(["relieve", "upgrade-agents", "--host", "ser6", "--kinds", "grok", "--probes-json", PROBES]) == 0
    # --no-rolling was dropped (rolling is the only mode); it must be a usage error now
    with pytest.raises(SystemExit):
        cli.main(["relieve", "upgrade-agents", "--no-rolling"])
    out = capsys.readouterr().out
    assert "session stop" not in out and "integration install grok" in out
    assert cli.main(["overhaul", "upgrade-herdr", "--host", "ser6", "--probes-json", PROBES]) == 3
    assert "pacman" in capsys.readouterr().out


def test_status_and_doctor(xdg, fixture_collect, capsys):
    assert cli.main(["status"]) == 1
    assert "no current roster" in capsys.readouterr().out
    assert cli.main(["doctor", "--probes-json", PROBES]) == 0
    assert "handoff=unsupported" in capsys.readouterr().out
