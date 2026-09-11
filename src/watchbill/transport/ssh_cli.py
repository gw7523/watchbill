"""DEFAULT remote transport: ``ssh BatchMode`` + remote ``herdr --session S``.

    ssh -o BatchMode=yes -o ConnectTimeout=5 <target> -- herdr --session S <args>

Why not ``herdr --remote``: Omarchy's 0.8.2 package has reported ``0.8.0``
while speaking protocol 20, and ``--remote`` refuses on a version mismatch.
The CLI-over-SSH path only needs the *remote* herdr to match its own server.

Remote argv is shell-quoted with :func:`shlex.join` because sshd hands the
command string to the login shell. ``BatchMode=yes`` means a missing key or a
host-key prompt fails fast instead of hanging a rolling relieve.
"""
from __future__ import annotations

import shlex
from typing import Sequence

from ..hosts import Host
from .base import CmdResult, NotImplementedInThisPass, backend_for, mux_prefix


def ssh_prefix(host: Host) -> list[str]:
    if not host.target:
        raise ValueError(f"host {host.name}: ssh_cli transport needs a target")
    return ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={host.connect_timeout}", host.target, "--"]


class SshCliSession:
    def __init__(self, host: Host, session: str):
        self.host = host
        self.session = session
        self.backend = backend_for(host)

    def mux_argv(self, *args: str) -> list[str]:
        remote = [*mux_prefix(self.host, self.session), *args]
        return [*ssh_prefix(self.host), shlex.join(remote)]

    herdr_argv = mux_argv

    def shell_argv(self, argv: Sequence[str]) -> list[str]:
        return [*ssh_prefix(self.host), shlex.join(list(argv))]

    def mux(self, *args: str, timeout: float = 30.0) -> CmdResult:
        raise NotImplementedInThisPass(f"ssh_cli {self.host.mux} {' '.join(args)} on {self.host.name}")

    herdr = mux

    def shell(self, argv: Sequence[str], timeout: float = 60.0) -> CmdResult:
        raise NotImplementedInThisPass(f"ssh_cli shell on {self.host.name}")

    def reachable(self) -> bool:
        raise NotImplementedInThisPass(f"ssh_cli reachability probe for {self.host.name}")
