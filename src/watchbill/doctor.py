"""Host health and install-flavor probe.

``assess`` is pure and takes raw facts; ``check`` gathers them through a
transport. The one rule that matters most: **``capabilities.live_handoff``
from ``herdr status server --json`` is ``true`` on a pacman install**, so
``handoff_supported`` is only true when the flavor is ``official`` *and* the
server advertises it. Live relieve errors on anything else.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .transport.base import HostSession

FLAVORS = ("pacman", "mise", "brew", "nix", "official", "unknown")


@dataclass(frozen=True)
class Probe:
    host: str
    reachable: bool = True
    version: str | None = None
    protocol: int | None = None
    compatible: bool | None = None
    running: bool = False
    restart_needed: bool = False
    flavor: str = "unknown"
    herdr_path: str | None = None
    live_handoff_flag: bool = False
    handoff_supported: bool = False
    agent_versions: dict[str, str] = field(default_factory=dict)
    agent_flavors: dict[str, str] = field(default_factory=dict)
    error: str | None = None

    @property
    def summary(self) -> str:
        if not self.reachable:
            return f"{self.host}: unreachable ({self.error})"
        hand = "supported" if self.handoff_supported else "unsupported"
        return (f"{self.host}: herdr {self.version} proto {self.protocol} flavor={self.flavor} "
                f"running={self.running} handoff={hand}")


def flavor_from(path: str | None, *, pacman_owned: bool = False, home: str = "") -> str:
    """Install flavor from the herdr binary path and package ownership."""
    if not path:
        return "unknown"
    if pacman_owned:
        return "pacman"
    if "/.local/share/mise/" in path:
        return "mise"
    if "/opt/homebrew/" in path or "/Cellar/" in path or "/linuxbrew/" in path:
        return "brew"
    if path.startswith("/nix/store/"):
        return "nix"
    if path in (f"{home}/.local/bin/herdr", "/usr/local/bin/herdr"):
        return "official"
    return "unknown"


def tool_flavor(path: str | None, *, pacman_owned: bool = False) -> str:
    """Flavor of an agent CLI (claude, codex, …) for upgrade-agents."""
    if not path:
        return "unknown"
    if pacman_owned:
        return "pacman"
    if "/.local/share/mise/" in path:
        return "mise"
    if "/node_modules/" in path or "/npm/" in path or "/lib/node" in path:
        return "npm"
    if "/opt/homebrew/" in path or "/Cellar/" in path:
        return "brew"
    return "unknown"


def assess(host: str, status: dict | None, herdr_path: str | None, *, pacman_owned: bool = False,
           home: str = "", agent_versions: dict[str, str] | None = None,
           agent_paths: dict[str, str] | None = None, error: str | None = None) -> Probe:
    if status is None:
        return Probe(host=host, reachable=error is None, herdr_path=herdr_path,
                     flavor=flavor_from(herdr_path, pacman_owned=pacman_owned, home=home), error=error)
    caps = status.get("capabilities") or {}
    flavor = flavor_from(herdr_path, pacman_owned=pacman_owned, home=home)
    flag = bool(caps.get("live_handoff"))
    return Probe(
        host=host, reachable=True, version=status.get("version"), protocol=status.get("protocol"),
        compatible=status.get("compatible"), running=bool(status.get("running")),
        restart_needed=bool(status.get("restart_needed")), flavor=flavor, herdr_path=herdr_path,
        live_handoff_flag=flag, handoff_supported=(flavor == "official" and flag),
        agent_versions=dict(agent_versions or {}),
        agent_flavors={k: tool_flavor(p) for k, p in (agent_paths or {}).items()},
    )


def check(hs: HostSession, *, kinds: tuple[str, ...] = ()) -> Probe:
    """Live probe over a transport. Read-only commands only: the mux's status
    verb, ``command -v <mux>``, ``pacman -Qo``, ``<agent> --version``.
    Raises NotImplementedInThisPass until MVP."""
    be = hs.backend
    mux_bin = be.name
    st = hs.mux(*be.status_argv())
    path = hs.shell(["sh", "-c", f"command -v {mux_bin}"])
    owner = hs.shell(["sh", "-c", f"pacman -Qo \"$(command -v {mux_bin})\" >/dev/null 2>&1 && echo yes || echo no"])
    versions: dict[str, str] = {}
    apaths: dict[str, str] = {}
    for kind in kinds:
        from .classify import AGENT_KINDS
        exe = AGENT_KINDS.get(kind, kind)
        v = hs.shell(["sh", "-c", f"{exe} --version 2>/dev/null | head -1"])
        p = hs.shell(["sh", "-c", f"command -v {exe}"])
        if v.ok and v.stdout.strip():
            versions[kind] = v.stdout.strip()
        if p.ok and p.stdout.strip():
            apaths[kind] = p.stdout.strip()
    status = be.parse_status(st.stdout, st.ok)
    raw = status.raw if be.name == "herdr" else {"running": status.running, "version": status.version, "socket": status.socket,
                                                 "capabilities": {"live_handoff": False}}
    probe = assess(hs.host.name, raw if st.ok else None, path.stdout.strip() or None,
                   pacman_owned=owner.stdout.strip() == "yes", agent_versions=versions, agent_paths=apaths,
                   error=None if st.ok else st.stderr.strip() or "status failed")
    if be.caps.live_handoff == "never":
        probe = Probe(**{**probe.__dict__, "handoff_supported": False})
    return probe


def expect_version(probe: Probe, expected: str | None) -> str | None:
    """Return an error string when the post-action version does not match."""
    if expected and probe.version != expected:
        return f"{probe.host}: expected herdr {expected}, found {probe.version}"
    return None
