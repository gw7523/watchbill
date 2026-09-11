"""Failing-red acceptance tests: live transport, collect, doctor, exec.
Strict xfail so they are *expected* to fail against the skeleton and the
suite breaks the day one of them silently passes without the MVP."""
import pytest

from watchbill import collect, doctor, hosts
from watchbill.transport import make_session

MVP = pytest.mark.xfail(strict=True, raises=Exception, reason="MVP: live local/ssh_cli transport not implemented in this pass")


@MVP
def test_local_transport_reads_server_status():
    hs = make_session(hosts.default_fleet("rig2").hosts[0], "default")
    assert hs.herdr("status", "server", "--json").json()["protocol"] == 20


@MVP
def test_gather_host_local_is_reachable():
    hf = collect.gather_host(hosts.default_fleet("rig2").hosts[0])
    assert hf.reachable and hf.sessions and hf.sessions[0].snapshot


@MVP
def test_doctor_check_local():
    p = doctor.check(make_session(hosts.default_fleet("rig2").hosts[0], "default"))
    assert p.version == "0.8.2" and p.flavor in doctor.FLAVORS


@MVP
def test_ssh_cli_reachability_probe():
    h = hosts.Host(name="ser6", target="ser6.example")
    assert make_session(h, "default").reachable() in (True, False)
