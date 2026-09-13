"""Local transport: the cockpit's own multiplexer server(s).

Runs the argv directly on this machine. The mux CLI is spawned without a
shell; ``scrub_for`` drops the env vars that would redirect it (a Watchbill
running inside a tmux pane must not be told it is nested, and
``HERDR_SOCKET_PATH`` must not outrank the ``--session`` we pass).
"""
from __future__ import annotations

from typing import Sequence

from ..hosts import Host
from .base import CmdResult, backend_for, mux_prefix, run_argv, scrub_for


class LocalSession:
    def __init__(self, host: Host, session: str):
        self.host = host
        self.session = session
        self.backend = backend_for(host)

    def mux_argv(self, *args: str) -> list[str]:
        return [*self.host.exec_prefix, *mux_prefix(self.host, self.session), *args]

    herdr_argv = mux_argv

    def shell_argv(self, argv: Sequence[str]) -> list[str]:
        return [*self.host.exec_prefix, *argv]

    def mux(self, *args: str, timeout: float = 30.0) -> CmdResult:
        return run_argv(self.mux_argv(*args), timeout=timeout, scrub=scrub_for(self.host))

    herdr = mux

    def shell(self, argv: Sequence[str], timeout: float = 60.0) -> CmdResult:
        return run_argv(self.shell_argv(argv), timeout=timeout, scrub=scrub_for(self.host))

    def reachable(self) -> bool:
        return True
