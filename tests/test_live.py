"""Live acceptance tests: the MVP transports against a real multiplexer.

These were the strict-xfail "red" tests of the architecture pass. They now
run for real and are the MVP's own oracle. They are **skipped** where the
mux is not running, so the suite stays green on a box without one, and they
are strictly read-only: nothing here parks, stops, or types into a pane.

Run only these:  uv run pytest -m live -q
Skip them:       uv run pytest -m "not live" -q
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

from watchbill import collect, doctor, hosts, mux
from watchbill.transport import make_session
from watchbill.transport.base import run_argv

live = pytest.mark.live


def herdr_running() -> bool:
    if not shutil.which("herdr"):
        return False
    try:
        out = subprocess.run(["herdr", "--session", "default", "status", "server", "--json"],
                             capture_output=True, text=True, timeout=10)
        return out.returncode == 0 and '"running":true' in out.stdout.replace(" ", "")
    except (OSError, subprocess.SubprocessError):
        return False


needs_herdr = pytest.mark.skipif(not herdr_running(), reason="no running herdr server on this box")
needs_tmux = pytest.mark.skipif(not shutil.which("tmux"), reason="tmux not installed")


@pytest.fixture(scope="module")
def cockpit():
    return hosts.default_fleet(hosts.local_hostname()).hosts[0]


# -- transport ------------------------------------------------------------

@live
@needs_herdr
def test_local_transport_reads_server_status(cockpit):
    st = make_session(cockpit, "default").mux("status", "server", "--json")
    assert st.ok and st.json()["protocol"] == 20


@live
@needs_herdr
def test_local_shell_primitive(cockpit):
    assert make_session(cockpit, "default").shell(["sh", "-c", "command -v herdr"]).stdout.strip().endswith("herdr")


@live
def test_run_argv_never_raises():
    """A missing binary and a timeout come back as failed results, not exceptions."""
    miss = run_argv(["watchbill-no-such-binary"], timeout=5)
    assert not miss.ok and miss.returncode == 127 and "not found" in miss.stderr
    slow = run_argv(["sh", "-c", "sleep 5"], timeout=0.3)
    assert not slow.ok and slow.returncode == 124 and "timed out" in slow.stderr


@live
@needs_tmux
def test_tmux_env_is_scrubbed_so_a_nested_server_is_not_confused():
    """`TMUX` in the environment makes tmux believe it is nested; the local
    transport drops it. Uses a throwaway server and kills it."""
    h = hosts.Host(name="probe", transport="local", mux="tmux", sessions=["wbtest"])
    hs = make_session(h, "wbtest")
    try:
        made = hs.mux("new-session", "-d", "-s", "probe", "-c", "/tmp", "-P", "-F", "#{session_id}|#{pane_id}")
        assert made.ok, made.stderr
        ids = hs.backend.created_ids(made.stdout)
        assert ids["pane_id"].startswith("%") and ids["workspace_id"].startswith("$")
        snap = hs.backend.parse_snapshot([hs.mux(*a).stdout for a in hs.backend.snapshot_argvs()])
        assert [w.label for w in snap.workspaces] == ["probe"] and snap.panes[0].pane_id == ids["pane_id"]
    finally:
        hs.mux("kill-server")


@live
@needs_herdr
def test_herdr_verbs_against_an_isolated_probe_session():
    """Start a throwaway *named* herdr session, exercise the verbs `set` and
    `secure` depend on, then stop it. Never touches the `default` session
    where real agents live. Each assertion is a fact recorded in
    docs/herdr-0.8.2-facts.md."""
    import time
    be = mux.get("herdr")
    name = "wbtestprobe"
    host = hosts.Host(name="probe", transport="local", mux="herdr", sessions=[name])
    hs = make_session(host, name)
    # headless start (verified 2026-09-11): `herdr --session <name> server`
    subprocess.Popen(["setsid", "herdr", "--session", name, "server"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    try:
        for _ in range(40):
            if hs.mux(*be.status_argv()).ok:
                break
            time.sleep(0.5)
        assert hs.mux(*be.status_argv()).ok, "probe server never came up"

        ws = hs.mux(*be.workspace_create("probe-ws", "/tmp"))
        assert ws.ok, ws.stderr
        ids = be.created_ids(ws.stdout)
        pane, wsid = ids["pane_id"], ids["workspace_id"]
        assert pane.endswith(":p1") and wsid

        tab = hs.mux(*be.tab_create(wsid, "probe-tab", "/tmp"))
        assert tab.ok and be.created_ids(tab.stdout)["pane_id"] != pane

        sp = hs.mux(*be.pane_split(pane, "right", "/tmp"))
        assert sp.ok and be.created_ids(sp.stdout)["pane_id"] not in (pane, "")

        # key names: `enter` and `C-c` are accepted, `ctrl-c` is not
        assert hs.mux(*be.send_text(pane, "echo watchbill-live-probe")).ok
        assert hs.mux(*be.send_enter(pane)).ok
        assert hs.mux(*be.interrupt(pane)).ok
        assert not hs.mux("pane", "send-keys", pane, "ctrl-c").ok, "ctrl-c should be rejected as invalid_key"

        # an error is exit!=0 with the payload on stderr — CmdResult.ok is trustworthy
        bad = hs.mux("agent", "get", pane)
        assert not bad.ok and "agent_not_found" in bad.stderr

        # the exit oracle: poll `agent get` until it fails. No agent here, so it ends at once.
        wait = be.resolve_poll(name, be.agent_wait_exit(pane, "claude", 5000))
        assert hs.shell(wait).ok

        # excerpts come from the visible viewport, not `recent` (which is empty on a settled pane)
        vis = hs.mux(*be.excerpt_argv(pane, 10))
        assert vis.ok and "watchbill-live-probe" in vis.stdout
        assert hs.mux("pane", "read", pane, "--source", "recent", "--lines", "10", "--format", "text").stdout.strip() == ""

        # a full roster of the probe session, built through the real collector
        from watchbill.slots import SlotStore
        hf = collect.gather_host(host)
        r = collect.build_roster("probe", [hf], slots=SlotStore())
        assert {o.workspace_label for o in r.occupants} == {"probe-ws"}
        assert all(o.role == "shell" for o in r.occupants), "no agents were started in the probe session"
        assert len(r.occupants) == 3            # root pane + tab root + split
    finally:
        hs.mux("server", "stop")
        subprocess.run(["herdr", "session", "delete", name], capture_output=True, timeout=10)


# -- collect / doctor -----------------------------------------------------

@live
@needs_herdr
def test_gather_host_and_build_a_real_roster(cockpit):
    hf = collect.gather_host(cockpit)
    assert hf.reachable and hf.sessions and hf.sessions[0].snapshot
    assert hf.herdr_path and hf.herdr_path.endswith("herdr")
    from watchbill.slots import SlotStore
    r = collect.build_roster("live", [hf], slots=SlotStore())
    assert r.occupants, "a live cockpit always has at least the pane watchbill runs in"
    me = [o for o in r.occupants if o.live_ids.pane_id]
    assert all(o.human_id.count("/") == 4 for o in me)
    assert all(o.slot_id and len(o.slot_id) == 26 for o in me)
    for o in r.occupants:
        if o.role == "agent":
            assert o.kind and o.agent_status in ("idle", "working", "blocked", "done", "unknown")


@live
@needs_herdr
def test_doctor_reports_flavor_and_refuses_handoff_on_a_package(cockpit):
    p = doctor.check(make_session(cockpit, "default"))
    assert p.reachable and p.running and p.protocol == 20
    assert p.flavor in doctor.FLAVORS
    if p.flavor in ("pacman", "mise", "brew", "nix"):
        # the server advertises the capability; only an official install may use it
        assert not p.handoff_supported
