"""omarchy-update: the Omarchy "Update > Omarchy" equivalent, unattended.

``omarchy-update`` without ``-y`` opens a TUI confirm dialog and blocks
forever from automation (it also self-escalates with sudo and takes a
snapshot). Watchbill only ever emits ``omarchy-update -y``; the operator's
``--yes`` is what lets exec run it. Refused on non-pacman hosts.
"""
from __future__ import annotations

from ..doctor import Probe
from ..hosts import Host
from .base import ActionUnavailable, BaseAction, BlastRadius, RemoteCmd


class OmarchyUpdate(BaseAction):
    name = "omarchy-update"

    def blast_radius(self) -> BlastRadius:
        # omarchy-update -y does not reboot by itself; it reports "restart needed".
        return BlastRadius(park_roles=frozenset({"agent"}), needs_session_stop=True, needs_client_attach=True)

    def commands(self, host: Host, probe: Probe) -> list[RemoteCmd]:
        if probe.flavor != "pacman":
            raise ActionUnavailable(f"{host.name}: omarchy-update needs a pacman/Omarchy host (flavor={probe.flavor})")
        return [RemoteCmd(("omarchy-update", "-y"), "Omarchy + system package update, unattended")]
