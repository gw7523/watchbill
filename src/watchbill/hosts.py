"""``~/.config/watchbill/hosts.toml`` — the fleet definition.

.. code-block:: toml

    [fleet]
    name = "home"

    [[host]]
    name = "rig2"          # roster host name; must be unique
    cockpit = true         # the machine Watchbill runs on; skipped unless --include-local
    transport = "local"
    sessions = ["default"]

    [[host]]
    name = "ser6"
    target = "ser6.tail1234.ts.net"   # ssh target (user@host ok)
    transport = "ssh_cli"             # default
    mux = "herdr"                     # herdr (default) | tmux | cmux — which multiplexer owns the PTYs
    sessions = ["default"]            # herdr session names, or tmux server socket names (-L)
    start  = "systemctl --user start herdr.service"   # optional; default is UNVERIFIED-0.8.2
    attach = "herdr session attach {session}"        # optional viewport command (#2064)
    herdr_bin = "herdr"

Herdr 0.9 ``machine`` profiles are imported by an adapter as extra ``[[host]]``
rows; schema stays 1 and nothing here calls ``herdr machine``.
"""
from __future__ import annotations

import os
import socket
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

TRANSPORTS = ("local", "ssh_cli", "ssh_socket", "herdr_remote")
MUXES = ("herdr", "tmux", "cmux")

# Verified 2026-09-13: `herdr --session S server` runs in the FOREGROUND.
# Spawned directly, exec would block until its timeout and then kill the
# server it had just started, so the default start detaches it. `setsid` is
# Linux (util-linux); macOS has none, so fall back to a nohup'd background
# subshell with every descriptor redirected, which also lets ssh return.
DEFAULT_START = ("if command -v setsid >/dev/null 2>&1; "
                 "then setsid -f herdr --session {session} server </dev/null >/dev/null 2>&1; "
                 "else (nohup herdr --session {session} server </dev/null >/dev/null 2>&1 &); fi")
DEFAULT_ATTACH = "herdr session attach {session}"


@dataclass
class Host:
    name: str
    target: str | None = None
    transport: str = "ssh_cli"
    sessions: list[str] = field(default_factory=lambda: ["default"])
    cockpit: bool = False
    start: str = DEFAULT_START
    attach: str = DEFAULT_ATTACH
    herdr_bin: str = "herdr"
    connect_timeout: int = 5
    remote_verified: bool = False   # set by doctor when cockpit and remote herdr versions match
    mux: str = "herdr"              # herdr | tmux | cmux
    mux_options: dict = field(default_factory=dict)   # e.g. tmux idle_after_s, conf; cmux socket
    # exec_prefix: run every command for this host *inside an environment* on
    # the target, e.g. ["distrobox", "enter", "sfl", "--"]. The same Watchbill
    # then manages a seat on this machine or on another one over ssh.
    exec_prefix: list[str] = field(default_factory=list)
    # ssh_options: extra ssh arguments for this host, e.g. ["-i", "~/.ssh/id_x"]
    # when the target is an address with no ~/.ssh/config entry. Watchbill
    # never edits ~/.ssh itself.
    ssh_options: list[str] = field(default_factory=list)
    # exclude: globs matched against an occupant's command line and human_id.
    # A match is catalogued but never parked, relaunched or restored, and a
    # window that would stop or close what it runs in is refused.
    exclude: list[str] = field(default_factory=list)
    # ignore: globs like `exclude`, but the occupant is NOT protected. It is
    # never parked, relaunched or restored, yet a window may stop the session
    # it runs in; the plan names what that will end. Use it for "out of scope,
    # nothing critical" and `exclude` for "must survive".
    ignore: list[str] = field(default_factory=list)

    def excludes(self, cmdline: str, human_id: str) -> str | None:
        """The first exclude glob that matches, or None."""
        import fnmatch
        return next((g for g in self.exclude if fnmatch.fnmatchcase(cmdline, g) or fnmatch.fnmatchcase(human_id, g)), None)

    def start_cmd(self, session: str) -> str:
        return self.start.format(session=session)

    def attach_cmd(self, session: str) -> str:
        return self.attach.format(session=session)


@dataclass
class Fleet:
    name: str
    hosts: list[Host]

    def host(self, name: str) -> Host | None:
        return next((h for h in self.hosts if h.name == name), None)

    @property
    def cockpit(self) -> Host | None:
        return next((h for h in self.hosts if h.cockpit), None)


def local_hostname() -> str:
    return socket.gethostname().split(".")[0].lower()


def parse(text: str, *, hostname: str | None = None) -> Fleet:
    raw = tomllib.loads(text)
    name = (raw.get("fleet") or {}).get("name", "default")
    hosts: list[Host] = []
    seen: set[str] = set()
    for h in raw.get("host", []):
        if "name" not in h:
            raise ValueError("[[host]] needs a name")
        if h["name"] in seen:
            raise ValueError(f"duplicate host name {h['name']!r}")
        seen.add(h["name"])
        is_local = bool(h.get("cockpit")) or (hostname is not None and h["name"] == hostname)
        transport = h.get("transport", "local" if is_local else "ssh_cli")
        if transport not in TRANSPORTS:
            raise ValueError(f"host {h['name']}: unknown transport {transport!r}")
        if transport != "local" and not h.get("target"):
            raise ValueError(f"host {h['name']}: transport {transport} needs target")
        mux = h.get("mux", "herdr")
        if mux not in MUXES:
            raise ValueError(f"host {h['name']}: unknown mux {mux!r}")
        if mux != "herdr" and transport == "herdr_remote":
            raise ValueError(f"host {h['name']}: herdr_remote transport only reaches a herdr mux")
        exec_prefix = h.get("exec_prefix", [])
        if not isinstance(exec_prefix, list) or not all(isinstance(t, str) for t in exec_prefix):
            raise ValueError(f"host {h['name']}: exec_prefix must be a list of strings")
        if exec_prefix and transport == "herdr_remote":
            raise ValueError(f"host {h['name']}: herdr_remote cannot run inside an exec_prefix; use ssh_cli")
        ssh_options = h.get("ssh_options", [])
        if not isinstance(ssh_options, list) or not all(isinstance(t, str) for t in ssh_options):
            raise ValueError(f"host {h['name']}: ssh_options must be a list of strings")
        exclude = h.get("exclude", [])
        if not isinstance(exclude, list) or not all(isinstance(t, str) for t in exclude):
            raise ValueError(f"host {h['name']}: exclude must be a list of glob strings")
        ignore = h.get("ignore", [])
        if not isinstance(ignore, list) or not all(isinstance(t, str) for t in ignore):
            raise ValueError(f"host {h['name']}: ignore must be a list of glob strings")
        hosts.append(Host(
            name=h["name"], target=h.get("target"), transport=transport,
            sessions=list(h.get("sessions", ["default"])), cockpit=bool(h.get("cockpit", False)),
            start=h.get("start", DEFAULT_START), attach=h.get("attach", DEFAULT_ATTACH),
            herdr_bin=h.get("herdr_bin", "herdr"), connect_timeout=int(h.get("connect_timeout", 5)),
            mux=mux, mux_options=dict(h.get("mux_options", {})),
            exec_prefix=list(exec_prefix), exclude=list(exclude), ignore=list(ignore),
            ssh_options=[os.path.expanduser(t) for t in ssh_options],
        ))
    if hostname and not any(x.cockpit for x in hosts):
        # If the running machine is listed by name, it is the cockpit.
        for x in hosts:
            if x.name == hostname:
                x.cockpit = True
    return Fleet(name=name, hosts=hosts)


def default_fleet(hostname: str | None = None) -> Fleet:
    """No hosts.toml: a one-host fleet made of this machine."""
    hn = hostname or local_hostname()
    return Fleet(name="default", hosts=[Host(name=hn, transport="local", cockpit=True)])


def load(path: Path | None = None, *, hostname: str | None = None) -> Fleet:
    from . import paths
    p = path or paths.hosts_file()
    if not p.exists():
        return default_fleet(hostname)
    return parse(p.read_text(), hostname=hostname or local_hostname())
