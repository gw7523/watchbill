"""upgrade-mux (alias: upgrade-herdr): new multiplexer binary. Parks agents,
stops the session (cold). Per mux:

* herdr — flavor decides: official → ``herdr update``; mise/brew → their
  upgrade; pacman/Omarchy → refused unless ``relieve.allow_partial_pacman``
  (then ``pacman -S herdr``). **Never** ``herdr update`` on a package.
* tmux — the running server keeps the old binary, so the window is cold:
  park, ``kill-server``, ``brew upgrade tmux`` / ``pacman -S tmux``,
  ``new-session -d``, set.
* cmux — park, quit the app, ``brew upgrade --cask cmux`` (UNVERIFIED-LIVE:
  never run), ``open -a cmux``; the app restores its workspaces and resumes
  its hook-tracked agents itself.
"""
from __future__ import annotations

from ..doctor import Probe
from ..hosts import Host
from .base import ActionUnavailable, BaseAction, BlastRadius, RemoteCmd


class UpgradeMux(BaseAction):
    name = "upgrade-mux"

    def blast_radius(self) -> BlastRadius:
        return BlastRadius(park_roles=frozenset({"agent"}), needs_session_stop=True, needs_client_attach=True)

    def commands(self, host: Host, probe: Probe) -> list[RemoteCmd]:
        live = self.options.get("mode") == "live"
        if host.mux == "tmux":
            if probe.flavor == "pacman":
                if not self.options.get("allow_partial_pacman"):
                    raise ActionUnavailable(f"{host.name}: tmux is pacman-owned; use omarchy-update or set relieve.allow_partial_pacman = true")
                return [RemoteCmd(("sudo", "pacman", "-S", "--noconfirm", "tmux"), "partial pacman upgrade of tmux", before_stop=False)]
            if probe.flavor == "brew":
                return [RemoteCmd(("brew", "upgrade", "tmux"), "brew upgrade tmux", before_stop=False)]
            raise ActionUnavailable(f"{host.name}: tmux install flavor {probe.flavor!r} has no known upgrade path")
        if host.mux == "cmux":
            return [RemoteCmd(("brew", "upgrade", "--cask", "cmux"), "brew upgrade --cask cmux (UNVERIFIED-LIVE)",
                              before_stop=False, unverified=True)]
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


UpgradeHerdr = UpgradeMux   # alias for the contract's name
