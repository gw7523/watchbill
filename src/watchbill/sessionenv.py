"""The environment a multiplexer SERVER runs with, recorded and restored.

Agents inherit their pane's environment, and panes inherit the server's. A
server started from a desktop carries the user's session (Wayland/X display,
desktop name, session bus); a server started over ssh carries the ssh
connection instead. Verified on ser6 (2026-09-13): after Watchbill restarted
`default` over ssh, the new server had no WAYLAND_DISPLAY, XDG_SESSION_TYPE=tty
and SSH_CONNECTION set, so resumed agents could no longer reach the desktop
(clipboard, browser opens, notifications) although their own flags were right.

Policy: a mux server is a desktop-session service. When Watchbill starts one
it (1) never passes the ssh connection variables on, (2) exports the display
variables recorded from the previous server, (3) overwrites them with the
user's *live* session manager environment where one exists (systemd user
manager: authoritative, since a display or compositor may have restarted), and
(4) drops any display variable whose socket or directory no longer exists. On
a headless host none of that adds anything.
"""
from __future__ import annotations

import shlex

READONLY_MARK = ": watchbill-readonly;"
DISPLAY_VARS = ("WAYLAND_DISPLAY", "DISPLAY", "XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP", "XDG_SESSION_DESKTOP",
                "HYPRLAND_INSTANCE_SIGNATURE", "SWAYSOCK", "DBUS_SESSION_BUS_ADDRESS")
SSH_VARS = ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY")


def server_env_probe_argv(shell_pid: int | None = None, server_pid: int | None = None) -> list[str]:
    """Read the server's environment on the target. herdr: the server is the
    parent of any pane's shell. tmux: its pid is known directly."""
    keep = "|".join(DISPLAY_VARS)
    pid = f"{int(server_pid)}" if server_pid else f"$(ps -o ppid= -p {int(shell_pid)} | tr -d ' ')"
    body = (f"{READONLY_MARK} p={pid}; tr '\\0' '\\n' </proc/$p/environ 2>/dev/null | "
            f"while IFS= read -r l; do n=${{l%%=*}}; case \"$n\" in {keep}) printf 'V %s\\n' \"$l\";; "
            "SSH_CONNECTION) printf 'S\\n';; esac; done")
    return ["sh", "-c", body]


def parse_server_env(stdout: str) -> tuple[dict[str, str], bool]:
    env: dict[str, str] = {}
    via_ssh = False
    for line in stdout.splitlines():
        if line == "S":
            via_ssh = True
        elif line.startswith("V ") and "=" in line:
            k, _, v = line[2:].partition("=")
            if k in DISPLAY_VARS:
                env[k] = v
    return env, via_ssh


def start_prelude(recorded: dict[str, str] | None) -> str:
    """Shell run immediately before a server start, on the target."""
    parts = ["unset " + " ".join(SSH_VARS)]
    for k, v in sorted((recorded or {}).items()):
        if k in DISPLAY_VARS:
            parts.append(f"export {k}={shlex.quote(v)}")
    grep = "|".join(DISPLAY_VARS)
    parts.append("if command -v systemctl >/dev/null 2>&1; then _wb_t=$(mktemp); "
                 f"systemctl --user show-environment 2>/dev/null | grep -E '^({grep})=' > \"$_wb_t\"; "
                 "while IFS= read -r _wb_l; do export \"$_wb_l\"; done < \"$_wb_t\"; rm -f \"$_wb_t\"; fi")
    parts.append("_wb_r=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}")
    parts.append('if [ -n "${WAYLAND_DISPLAY:-}" ] && [ ! -S "$_wb_r/$WAYLAND_DISPLAY" ]; then unset WAYLAND_DISPLAY; fi')
    parts.append('if [ -n "${HYPRLAND_INSTANCE_SIGNATURE:-}" ] && [ ! -d "$_wb_r/hypr/$HYPRLAND_INSTANCE_SIGNATURE" ]; '
                 'then unset HYPRLAND_INSTANCE_SIGNATURE; fi')
    parts.append('if [ -n "${SWAYSOCK:-}" ] && [ ! -S "$SWAYSOCK" ]; then unset SWAYSOCK; fi')
    return "; ".join(parts) + "; "
