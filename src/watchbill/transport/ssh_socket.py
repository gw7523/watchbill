"""LATER: forward the remote Herdr socket and speak JSON-RPC directly.

Needed for ``layout.export`` / ``layout.apply`` (socket-only in 0.8.2; no
``herdr layout`` CLI group exists) and for ``events.subscribe``. Not part of
MVP; ``ssh_cli`` rebuilds shape with ``workspace create`` + ``pane split``.
"""
from __future__ import annotations

from typing import Sequence

from ..hosts import Host
from .base import CmdResult, NotImplementedInThisPass


class SshSocketSession:
    def __init__(self, host: Host, session: str):
        self.host = host
        self.session = session

    def herdr_argv(self, *args: str) -> list[str]:
        raise NotImplementedInThisPass("ssh_socket transport")

    def shell_argv(self, argv: Sequence[str]) -> list[str]:
        raise NotImplementedInThisPass("ssh_socket transport")

    def herdr(self, *args: str, timeout: float = 30.0) -> CmdResult:
        raise NotImplementedInThisPass("ssh_socket transport")

    def shell(self, argv: Sequence[str], timeout: float = 60.0) -> CmdResult:
        raise NotImplementedInThisPass("ssh_socket transport")

    def reachable(self) -> bool:
        raise NotImplementedInThisPass("ssh_socket transport")
