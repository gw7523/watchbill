"""restart-harness: plugin / integration / config needs a bounce.

Same shape as restart-herdr (park agents, session stop, start, attach, set)
but named for its intent so a chief-of-staff agent can say "install plugin X
then restart-harness" without reaching for the upgrader.
"""
from __future__ import annotations

from ..doctor import Probe
from ..hosts import Host
from .base import BaseAction, BlastRadius, RemoteCmd


class RestartHarness(BaseAction):
    name = "restart-harness"

    def blast_radius(self) -> BlastRadius:
        return BlastRadius(park_roles=frozenset({"agent"}), needs_session_stop=True, needs_client_attach=True)

    def commands(self, host: Host, probe: Probe) -> list[RemoteCmd]:
        return []
