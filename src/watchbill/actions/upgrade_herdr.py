"""upgrade-herdr: new herdr server binary. Parks agents, stops the session.

Flavor decides the command; ``herdr update`` is emitted **only** for the
official-installer flavor. A pacman/Omarchy host is refused unless
``relieve.allow_partial_pacman = true`` (then ``pacman -S herdr``, a partial
upgrade Arch warns against) — the supported path there is ``omarchy-update``.
"""
from __future__ import annotations

from ..doctor import Probe
from ..hosts import Host
from .base import ActionUnavailable, BaseAction, BlastRadius, RemoteCmd


class UpgradeHerdr(BaseAction):
    name = "upgrade-herdr"

    def blast_radius(self) -> BlastRadius:
        return BlastRadius(park_roles=frozenset({"agent"}), needs_session_stop=True, needs_client_attach=True)

    def commands(self, host: Host, probe: Probe) -> list[RemoteCmd]:
        live = self.options.get("mode") == "live"
        if probe.flavor == "pacman":
            if self.options.get("allow_partial_pacman"):
                return [RemoteCmd(("sudo", "pacman", "-S", "--noconfirm", "herdr"),
                                  "partial pacman upgrade of herdr (allow_partial_pacman=true)", before_stop=False)]
            raise ActionUnavailable(
                f"{host.name}: herdr is pacman-owned; refusing partial upgrade. Use the omarchy-update "
                "action, or set relieve.allow_partial_pacman = true. (never `herdr update` here)")
        if probe.flavor == "mise":
            return [RemoteCmd(("mise", "upgrade", "herdr"), "mise upgrade herdr", before_stop=False)]
        if probe.flavor == "brew":
            return [RemoteCmd(("brew", "upgrade", "herdr"), "brew upgrade herdr", before_stop=False)]
        if probe.flavor == "official":
            argv = (host.herdr_bin, "update", "--handoff") if live else (host.herdr_bin, "update")
            # live: the server must still be running for --handoff; cold: after session stop
            return [RemoteCmd(argv, "official installer update" + (" with live handoff" if live else ""), before_stop=live)]
        raise ActionUnavailable(f"{host.name}: herdr install flavor {probe.flavor!r} has no known upgrade path")
