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
    mux: str = "herdr"
    warnings: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if not self.reachable:
            return f"{self.host}: unreachable ({self.error})"
        hand = "supported" if self.handoff_supported else "unsupported"
        proto = f" proto {self.protocol}" if self.protocol is not None else ""
        warn = "".join(f"\n  warning: {w}" for w in self.warnings)
        return (f"{self.host}: {self.mux} {self.version}{proto} flavor={self.flavor} "
                f"running={self.running} handoff={hand}{warn}")


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
    from .collect import probe_install
    be = hs.backend
    st = hs.mux(*be.status_argv())
    herdr_path, pacman_owned = probe_install(hs)
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
    version = status.version
    if st.ok and not version and hasattr(be, "version_argv"):
        # cmux: `ping` proves liveness but carries no version; `version` does
        vr = hs.mux(*be.version_argv())
        version = be.parse_version(vr.stdout) if vr.ok else None
    raw = status.raw if be.name == "herdr" else {"running": status.running, "version": version, "socket": status.socket,
                                                 "capabilities": {"live_handoff": False}}
    probe = assess(hs.host.name, raw if st.ok else None, herdr_path,
                   pacman_owned=pacman_owned, agent_versions=versions, agent_paths=apaths,
                   error=None if st.ok else st.stderr.strip() or "status failed")
    if be.caps.live_handoff == "never":
        probe = Probe(**{**probe.__dict__, "handoff_supported": False})
    warnings = cmux_warnings(hs) if (be.name == "cmux" and st.ok) else []
    return Probe(**{**probe.__dict__, "mux": be.name, "warnings": warnings})


def cmux_warnings(hs: HostSession) -> list[str]:
    """Read-only check for what a scripted cmux window needs: the quit
    confirmation off, or the quit step blocks on the dialog. cmux.json is
    JSONC; comment lines are skipped and the key may sit inline in `app`."""
    out: list[str] = []
    r = hs.shell(["sh", "-c", "grep -v '^[[:space:]]*//' ~/.config/cmux/cmux.json 2>/dev/null | "
                  "grep -o -E '\"(confirmQuit|warnBeforeQuit)\"[[:space:]]*:[[:space:]]*(\"[a-z-]*\"|true|false)'; true"])
    text = (r.stdout or "") if r.ok else ""
    if '"never"' not in text and not any(f"warnBeforeQuit{sp}false" in text.replace(" ", "") for sp in (":",)):
        out.append('app.confirmQuit is not "never" in ~/.config/cmux/cmux.json: a scripted quit (restart-harness, '
                   'upgrade-mux) will block on the confirmation dialog; set it and run `cmux reload-config`')
    return out
