"""Transports: the only code that knows local from SSH from ``--remote``.

``make_session(host, session)`` returns a :class:`HostSession`. Everything
above this package (collect, exec, doctor) calls ``hs.herdr(...)`` and
``hs.shell(...)`` and never spells ``ssh`` itself.

Default for non-cockpit hosts is :mod:`ssh_cli`. ``herdr_remote`` is opt-in
and only after doctor reports matching versions (Omarchy's 0.8.2 package has
reported ``0.8.0`` while speaking protocol 20, which breaks ``--remote``
version matching).
"""
from __future__ import annotations

from ..hosts import Host
from .base import CmdResult, HostSession, NotImplementedInThisPass


def make_session(host: Host, session: str) -> HostSession:
    if host.transport == "local":
        from .local import LocalSession
        return LocalSession(host, session)
    if host.transport == "ssh_cli":
        from .ssh_cli import SshCliSession
        return SshCliSession(host, session)
    if host.transport == "ssh_socket":
        from .ssh_socket import SshSocketSession
        return SshSocketSession(host, session)
    if host.transport == "herdr_remote":
        if not host.remote_verified:
            from .base import TransportError
            raise TransportError(f"{host.name}: herdr_remote needs a doctor version match first "
                                 "(Omarchy 0.8.2 packages break --remote matching); use ssh_cli")
        from .herdr_remote import HerdrRemoteSession
        return HerdrRemoteSession(host, session)
    raise ValueError(f"unknown transport {host.transport!r}")


__all__ = ["CmdResult", "HostSession", "NotImplementedInThisPass", "make_session"]
