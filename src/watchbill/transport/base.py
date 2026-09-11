"""``HostSession`` — the client protocol every transport implements.

One ``HostSession`` is one (host, herdr session) pair. It exposes two
primitives and two argv builders:

* ``herdr(*args)``   run ``herdr --session <S> <args>`` on the host
* ``shell(argv)``    run an arbitrary argv on the host (action commands)
* ``herdr_argv``/``shell_argv``  the exact argv the primitive *would* run —
  planners embed these in ``Step.argv`` so dry-run shows the literal command.

Transports do not police mutation; ``plan.is_mutating`` and ``exec`` do.
Transports never read OAuth or credential files and never rewrite base URLs.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol, Sequence

from ..exitcodes import TransportError  # noqa: F401  (re-exported for transports)
from ..hosts import Host


class NotImplementedInThisPass(TransportError):
    """Raised by live primitives in the architecture/skeleton pass."""

    def __init__(self, what: str):
        super().__init__(f"{what}: not implemented in this pass (architecture + skeleton). "
                         "Say 'implement MVP next' to add local + ssh_cli transports.")


@dataclass(frozen=True)
class CmdResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def json(self) -> dict:
        """Parse Herdr CLI JSON (``{"id": ..., "result": {...}}``) → result."""
        data = json.loads(self.stdout)
        if isinstance(data, dict) and "result" in data:
            return data["result"]
        return data


class HostSession(Protocol):
    host: Host
    session: str

    def herdr_argv(self, *args: str) -> list[str]: ...
    def shell_argv(self, argv: Sequence[str]) -> list[str]: ...
    def herdr(self, *args: str, timeout: float = 30.0) -> CmdResult: ...
    def shell(self, argv: Sequence[str], timeout: float = 60.0) -> CmdResult: ...
    def reachable(self) -> bool: ...


def herdr_session_args(host: Host, session: str) -> list[str]:
    """``herdr --session <S>`` prefix. The default session is still named
    explicitly so a remote ``HERDR_SESSION`` env cannot redirect us."""
    return [host.herdr_bin, "--session", session]
