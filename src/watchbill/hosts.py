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
    sessions = ["default"]
    start  = "systemctl --user start herdr.service"   # optional; default is UNVERIFIED-0.8.2
    attach = "herdr session attach {session}"        # optional viewport command (#2064)
    herdr_bin = "herdr"

Herdr 0.9 ``machine`` profiles are imported by an adapter as extra ``[[host]]``
rows; schema stays 1 and nothing here calls ``herdr machine``.
"""
from __future__ import annotations

import socket
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

TRANSPORTS = ("local", "ssh_cli", "ssh_socket", "herdr_remote")

# UNVERIFIED-0.8.2: headless session start. See docs/herdr-0.8.2-facts.md.
DEFAULT_START = "herdr --session {session} server"
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
        hosts.append(Host(
            name=h["name"], target=h.get("target"), transport=transport,
            sessions=list(h.get("sessions", ["default"])), cockpit=bool(h.get("cockpit", False)),
            start=h.get("start", DEFAULT_START), attach=h.get("attach", DEFAULT_ATTACH),
            herdr_bin=h.get("herdr_bin", "herdr"), connect_timeout=int(h.get("connect_timeout", 5)),
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
