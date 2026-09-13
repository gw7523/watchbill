"""The server's session environment is recorded and restored, never the ssh connection."""
import subprocess

from watchbill import sessionenv as E
from watchbill.plan_set import SetOptions, plan_set


def test_parse_keeps_display_vars_and_flags_ssh():
    env, ssh = E.parse_server_env("V WAYLAND_DISPLAY=wayland-1\nV DISPLAY=:0\nV XDG_SESSION_TYPE=wayland\nS\nV PATH=/x\n")
    assert env == {"WAYLAND_DISPLAY": "wayland-1", "DISPLAY": ":0", "XDG_SESSION_TYPE": "wayland"} and ssh


def test_probe_finds_the_server_as_the_parent_of_a_pane_shell():
    body = E.server_env_probe_argv(shell_pid=4242)[2]
    assert body.startswith(E.READONLY_MARK) and "ps -o ppid= -p 4242" in body and "/proc/$p/environ" in body


def run_prelude(recorded, extra=""):
    """Evaluate the prelude in a real sh with SSH vars set and no session manager, print the result."""
    script = E.start_prelude(recorded) + extra + 'printf "%s|%s|%s|%s" "${WAYLAND_DISPLAY:-}" "${DISPLAY:-}" "${SSH_CONNECTION:-}" "${XDG_SESSION_TYPE:-}"'
    env = {"PATH": "/usr/bin:/bin", "SSH_CONNECTION": "1.2.3.4 1 5.6.7.8 22", "SSH_CLIENT": "x", "XDG_RUNTIME_DIR": "/nonexistent-runtime"}
    return subprocess.run(["sh", "-c", script], capture_output=True, text=True, env=env).stdout


def test_ssh_connection_never_reaches_the_server_and_dead_displays_are_dropped():
    out = run_prelude({"WAYLAND_DISPLAY": "wayland-1", "DISPLAY": ":0", "XDG_SESSION_TYPE": "wayland"})
    wayland, display, ssh, stype = out.split("|")
    assert ssh == ""                          # never inherited from the ssh Watchbill arrived on
    assert wayland == ""                      # its socket does not exist under this runtime dir
    assert display == ":0" and stype == "wayland"


def test_a_live_wayland_socket_is_kept(tmp_path):
    import socket
    s = socket.socket(socket.AF_UNIX); s.bind(str(tmp_path / "wayland-7"))
    try:
        script = E.start_prelude({"WAYLAND_DISPLAY": "wayland-7"}) + 'printf "%s" "${WAYLAND_DISPLAY:-}"'
        out = subprocess.run(["sh", "-c", script], capture_output=True, text=True,
                             env={"PATH": "/usr/bin:/bin", "XDG_RUNTIME_DIR": str(tmp_path)}).stdout
        assert out == "wayland-7"
    finally:
        s.close()


def test_start_step_runs_the_prelude_before_the_server(roster, fleet, probes):
    down = dict(probes)
    down["ser6"] = probes["ser6"].__class__(**{**probes["ser6"].__dict__, "running": False})
    roster.shape_for("ser6", "default").server_env = {"WAYLAND_DISPLAY": "wayland-1"}
    p = plan_set(roster, fleet, SetOptions(targets=["ser6/default/sfl-site/1/p1"], cockpit_host="rig2", self_pane="w4:p1", probes=down))
    start = next(s for s in p.steps if s.id.endswith(".start") and s.kind.value == "shell")
    body = start.raw[2]
    assert body.index("unset SSH_CONNECTION") < body.index("export WAYLAND_DISPLAY=wayland-1") < body.index("systemctl --user show-environment")
    assert body.rstrip().endswith("systemctl --user start herdr.service")      # the host's own start still runs last
