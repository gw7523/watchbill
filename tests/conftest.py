"""Shared fixtures. No live Herdr anywhere in the suite.

``FakeSession`` implements the HostSession protocol over the recorded JSON
in ``tests/fixtures/fleet.json`` and records every argv it is asked to run,
so tests can assert that dry-run never reaches a mutating verb.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from watchbill import collect, doctor, hosts, paths
from watchbill.classify import Allowlist
from watchbill.plan import is_mutating
from watchbill.roster import Roster
from watchbill.slots import SlotStore
from watchbill.transport.base import CmdResult

FIXTURES = Path(__file__).parent / "fixtures"
HOSTS_TOML = """
[fleet]
name = "test"

[[host]]
name = "rig2"
cockpit = true
transport = "local"

[[host]]
name = "ser6"
target = "ser6.example"
transport = "ssh_cli"
sessions = ["default"]
start = "systemctl --user start herdr.service"

[[host]]
name = "vps"
target = "vps.example"
transport = "ssh_cli"
"""


@pytest.fixture(scope="session")
def fleet_json() -> dict:
    return json.loads((FIXTURES / "fleet.json").read_text())


@pytest.fixture(scope="session")
def probes() -> dict[str, doctor.Probe]:
    raw = json.loads((FIXTURES / "probes.json").read_text())
    return {k: doctor.Probe(**v) for k, v in raw.items()}


@pytest.fixture
def fleet() -> hosts.Fleet:
    return hosts.parse(HOSTS_TOML, hostname="rig2")


def facts_from_fixture(fleet_json: dict, *, pane_id_offset: int = 0) -> list[collect.HostFacts]:
    """Build HostFacts. ``pane_id_offset`` renumbers every pane id to simulate
    a new server generation (pitfall 5) while labels stay the same."""
    out = []
    for name, h in fleet_json.items():
        hf = collect.HostFacts(host=name, cockpit=h["cockpit"], herdr_path=h["herdr_path"], pacman_owned=h["pacman_owned"])
        for sname, s in h["sessions"].items():
            snap = json.loads(json.dumps(s["snapshot"]))
            pi = dict(s["process_info"])
            if pane_id_offset:
                def bump(pid: str) -> str:
                    ws, p = pid.split(":p")
                    return f"{ws}:p{int(p) + pane_id_offset}"
                for p in snap["panes"]:
                    p["pane_id"] = bump(p["pane_id"])
                for lay in snap["layouts"]:
                    lay["focused_pane_id"] = bump(lay["focused_pane_id"])
                    for p in lay["panes"]:
                        p["pane_id"] = bump(p["pane_id"])
                snap["focused_pane_id"] = bump(snap["focused_pane_id"])
                pi = {bump(k): v for k, v in pi.items()}
            hf.sessions.append(collect.SessionFacts(sname, status=s["status"], snapshot=snap, process_info=pi))
        out.append(hf)
    return out


@pytest.fixture
def facts(fleet_json) -> list[collect.HostFacts]:
    return facts_from_fixture(fleet_json)


@pytest.fixture
def allowlist() -> Allowlist:
    return Allowlist.parse("# watchers we trust\nwatchexec *\nnpm run dev\n")


@pytest.fixture
def slots(tmp_path) -> SlotStore:
    return SlotStore.load(tmp_path / "slots.json")


@pytest.fixture
def roster(facts, slots, allowlist) -> Roster:
    return collect.build_roster("test", facts, slots=slots, allowlist=allowlist,
                                cockpit={"host": "rig2", "pane_id": "w4:p1", "session": "default"},
                                now="2026-09-11T12:00:00+00:00")


SELF_PANE = "w4:p1"


class FakeSession:
    """HostSession over recorded JSON. Records argv; refuses to answer
    mutating verbs unless ``allow_mutation`` so a dry-run that leaks a
    mutation fails loudly."""

    def __init__(self, host, session, fleet_json, *, allow_mutation=False, agent_gone: set[str] | None = None):
        from watchbill.transport.base import backend_for
        self.host = host
        self.session = session
        self.backend = backend_for(host)
        self.data = fleet_json.get(host.name, {}).get("sessions", {}).get(session)
        self.allow_mutation = allow_mutation
        self.calls: list[tuple[str, ...]] = []
        self.agent_gone = agent_gone or set()
        self._n = 100

    def mux_argv(self, *args):
        return [*self.backend.cli_prefix(self.session), *args]

    herdr_argv = mux_argv

    def shell_argv(self, argv):
        return list(argv)

    def mux(self, *args, timeout=30.0):
        return self.herdr(*args, timeout=timeout)

    def herdr(self, *args, timeout=30.0):
        self.calls.append(("herdr", *args))
        argv = self.herdr_argv(*args)
        if self.backend.is_mutating(list(args)) and not self.allow_mutation:
            raise AssertionError(f"mutating verb reached the transport: {' '.join(argv)}")
        if self.data is None:
            return CmdResult(tuple(argv), 1, "", "no such host/session")
        a = list(args)
        if a[:3] == ["status", "server", "--json"]:
            return CmdResult(tuple(argv), 0, json.dumps(self.data["status"]))
        if a[:2] == ["api", "snapshot"]:
            return CmdResult(tuple(argv), 0, json.dumps({"id": "x", "result": {"snapshot": self.data["snapshot"]}}))
        if a[:2] == ["pane", "process-info"]:
            assert "--current" not in a, "pitfall 2: pane … --current is wrong in 0.8.2"
            pid = a[a.index("--pane") + 1]
            return CmdResult(tuple(argv), 0, json.dumps({"id": "x", "result": {"process_info": self.data["process_info"].get(pid, {})}}))
        if a[:2] == ["pane", "read"]:
            return CmdResult(tuple(argv), 0, "line one\nline two\n$ ")
        if a[:2] == ["agent", "get"]:
            return CmdResult(tuple(argv), 1 if a[2] in self.agent_gone else 0, "{}", "agent_not_found" if a[2] in self.agent_gone else "")
        if a[:2] == ["workspace", "create"]:
            self._n += 1
            return CmdResult(tuple(argv), 0, json.dumps({"id": "x", "result": {"workspace": {"workspace_id": f"w{self._n}"},
                                                                                "root_pane": {"pane_id": f"w{self._n}:p1"}}}))
        if a[:2] == ["pane", "split"]:
            self._n += 1
            return CmdResult(tuple(argv), 0, json.dumps({"id": "x", "result": {"pane": {"pane_id": f"w{self._n}:p2"}}}))
        return CmdResult(tuple(argv), 0, json.dumps({"id": "x", "result": {}}))

    def shell(self, argv, timeout=60.0):
        self.calls.append(("shell", *argv))
        if not self.allow_mutation and not (argv[:2] == ["sh", "-c"] and "command -v" in argv[2]):
            raise AssertionError(f"shell command reached the transport in dry-run: {argv}")
        return CmdResult(tuple(argv), 0, "")

    def reachable(self):
        return self.data is not None


@pytest.fixture
def fake_sessions(fleet_json):
    made = {}

    def factory(host, session, **kw):
        key = (host.name, session)
        if key not in made:
            made[key] = FakeSession(host, session, fleet_json, **kw)
        return made[key]
    factory.made = made
    return factory


@pytest.fixture
def xdg(tmp_path, monkeypatch):
    """Redirect every Watchbill path into tmp."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("HERDR_PANE_ID", SELF_PANE)
    paths.config_dir().mkdir(parents=True)
    paths.hosts_file().write_text(HOSTS_TOML)
    paths.allowlist_file().write_text("watchexec *\n")
    return tmp_path
