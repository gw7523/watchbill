"""LATER, opt-in: ``herdr --remote <target> --session S``.

Only selectable after ``watchbill doctor`` reports that the cockpit's herdr
version string matches the remote server's (the Omarchy 0.8.2 package has
reported ``0.8.0`` while speaking protocol 20). Never the default.
"""
from __future__ import annotations

from typing import Sequence

from ..hosts import Host
from .base import CmdResult, NotImplementedInThisPass


class HerdrRemoteSession:
    def __init__(self, host: Host, session: str):
        self.host = host
        self.session = session
        from .base import backend_for
        self.backend = backend_for(host)

    def mux_argv(self, *args: str) -> list[str]:
        return self.herdr_argv(*args)

    def mux(self, *args: str, timeout: float = 30.0) -> CmdResult:
        return self.herdr(*args, timeout=timeout)

    def herdr_argv(self, *args: str) -> list[str]:
        if not self.host.target:
            raise ValueError("herdr_remote needs a target")
        return [self.host.herdr_bin, "--remote", self.host.target, "--session", self.session, *args]

    def shell_argv(self, argv: Sequence[str]) -> list[str]:
        raise NotImplementedInThisPass("herdr_remote has no shell primitive; use ssh_cli for actions")

    def herdr(self, *args: str, timeout: float = 30.0) -> CmdResult:
        raise NotImplementedInThisPass("herdr_remote transport")

    def shell(self, argv: Sequence[str], timeout: float = 60.0) -> CmdResult:
        raise NotImplementedInThisPass("herdr_remote transport")

    def reachable(self) -> bool:
        raise NotImplementedInThisPass("herdr_remote transport")
