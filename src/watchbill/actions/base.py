"""Action protocol: maintenance-window plugins.

An action never stops a session, never parks an agent, never touches a pane.
It *declares* what the window needs (:class:`BlastRadius`) and *returns* the
exact argv it wants run (:class:`RemoteCmd`). ``plan_relieve`` composes
secure → action → set around it; ``exec`` applies it. Dry-run prints the argv.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..doctor import Probe
from ..exitcodes import RefusedPlan
from ..hosts import Host


@dataclass(frozen=True)
class BlastRadius:
    park_roles: frozenset[str]          # roles to park before the action
    needs_session_stop: bool            # herdr session stop <S> before, start after
    needs_client_attach: bool           # #2064: a viewport must exist before agents resume
    allow_reboot: bool = False          # action may reboot the host (never the cockpit)
    park_kinds: frozenset[str] | None = None  # restrict parked agents to these kinds

    def as_dict(self) -> dict:
        return {"park_roles": sorted(self.park_roles), "needs_session_stop": self.needs_session_stop,
                "needs_client_attach": self.needs_client_attach, "allow_reboot": self.allow_reboot,
                "park_kinds": sorted(self.park_kinds) if self.park_kinds else None}


@dataclass(frozen=True)
class RemoteCmd:
    argv: tuple[str, ...]
    description: str
    mutating: bool = True
    via: str = "shell"        # "shell" → HostSession.shell, "herdr" → HostSession.herdr (session-scoped)
    unverified: bool = False  # UNVERIFIED-0.8.2 flag; dry-run shows it


@dataclass(frozen=True)
class Verify:
    description: str
    commands: tuple[RemoteCmd, ...] = ()   # read-only
    expected_version: str | None = None


class ActionUnavailable(RefusedPlan):
    """The action cannot run on this host as configured (exit 3)."""


@dataclass
class ActionContext:
    options: dict = field(default_factory=dict)   # CLI flags: mode, kinds, plugin, cmd, expected_version, allow_partial_pacman …
    probes: dict[str, Probe] = field(default_factory=dict)


@runtime_checkable
class Action(Protocol):
    name: str

    def blast_radius(self) -> BlastRadius: ...
    def probe(self, host: Host) -> Probe: ...
    def commands(self, host: Host, probe: Probe) -> list[RemoteCmd]: ...
    def verify(self, host: Host) -> Verify: ...


class BaseAction:
    """Shared plumbing. ``probe`` returns the pre-collected doctor probe for
    the host (relieve runs doctor once per host, read-only, before planning)."""

    name = "base"

    def __init__(self, ctx: ActionContext | None = None):
        self.ctx = ctx or ActionContext()

    @property
    def options(self) -> dict:
        return self.ctx.options

    def probe(self, host: Host) -> Probe:
        p = self.ctx.probes.get(host.name)
        if p is None:
            raise ActionUnavailable(f"{self.name}: no doctor probe for host {host.name}; run watchbill doctor")
        return p

    def verify(self, host: Host) -> Verify:
        return Verify("herdr server answers and version matches",
                      (RemoteCmd((host.herdr_bin, "status", "server", "--json"), "server status", mutating=False, via="herdr"),),
                      self.options.get("expected_version"))


HERDR_STATUS = ("status", "server", "--json")
