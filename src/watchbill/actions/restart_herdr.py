"""restart-herdr: the binary is already replaced; bounce the server.

No commands of its own: the blast radius (session stop + start) *is* the
action. Verify checks the server answers with the expected version.
"""
from __future__ import annotations

from ..doctor import Probe
from ..hosts import Host
from .base import BaseAction, BlastRadius, RemoteCmd


class RestartHerdr(BaseAction):
    name = "restart-herdr"

    def blast_radius(self) -> BlastRadius:
        return BlastRadius(park_roles=frozenset({"agent"}), needs_session_stop=True, needs_client_attach=True)

    def commands(self, host: Host, probe: Probe) -> list[RemoteCmd]:
        return []
