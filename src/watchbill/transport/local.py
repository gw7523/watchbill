"""Local transport: the cockpit's own Herdr server(s).

Argv building is complete. Live execution lands with the MVP.
"""
from __future__ import annotations

from typing import Sequence

from ..hosts import Host
from .base import CmdResult, NotImplementedInThisPass, herdr_session_args


class LocalSession:
    def __init__(self, host: Host, session: str):
        self.host = host
        self.session = session

    def herdr_argv(self, *args: str) -> list[str]:
        return [*herdr_session_args(self.host, self.session), *args]

    def shell_argv(self, argv: Sequence[str]) -> list[str]:
        return list(argv)

    def herdr(self, *args: str, timeout: float = 30.0) -> CmdResult:
        raise NotImplementedInThisPass(f"local herdr {' '.join(args)}")

    def shell(self, argv: Sequence[str], timeout: float = 60.0) -> CmdResult:
        raise NotImplementedInThisPass(f"local shell {' '.join(argv)}")

    def reachable(self) -> bool:
        return True
