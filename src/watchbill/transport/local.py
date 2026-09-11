"""Local transport: the cockpit's own Herdr server(s).

Argv building is complete. Live execution lands with the MVP.
"""
from __future__ import annotations

from typing import Sequence

from ..hosts import Host
from .base import CmdResult, NotImplementedInThisPass, backend_for, mux_prefix


class LocalSession:
    def __init__(self, host: Host, session: str):
        self.host = host
        self.session = session
        self.backend = backend_for(host)

    def mux_argv(self, *args: str) -> list[str]:
        return [*mux_prefix(self.host, self.session), *args]

    herdr_argv = mux_argv

    def shell_argv(self, argv: Sequence[str]) -> list[str]:
        return list(argv)

    def mux(self, *args: str, timeout: float = 30.0) -> CmdResult:
        raise NotImplementedInThisPass(f"local {self.host.mux} {' '.join(args)}")

    herdr = mux

    def shell(self, argv: Sequence[str], timeout: float = 60.0) -> CmdResult:
        raise NotImplementedInThisPass(f"local shell {' '.join(argv)}")

    def reachable(self) -> bool:
        return True
