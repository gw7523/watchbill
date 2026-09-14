"""reload-config: re-read the multiplexer's config without dropping PTYs.

herdr: ``herdr --session S server reload-config``. tmux: ``tmux -L S
source-file ~/.tmux.conf``. Both are live: nothing parked, nothing stopped.
cmux: ``cmux reload-config`` (re-reads ~/.config/cmux/cmux.json and the
Ghostty config in place; verified 0.64.22). This is the action to reach for after
editing keybindings or theme; ``restart-harness`` is for changes that need
the server process replaced.
"""
from __future__ import annotations

from .. import mux as _mux
from ..doctor import Probe
from ..hosts import Host
from .base import ActionUnavailable, BaseAction, BlastRadius, RemoteCmd, Verify


class ReloadConfig(BaseAction):
    name = "reload-config"

    def blast_radius(self) -> BlastRadius:
        return BlastRadius(park_roles=frozenset(), needs_session_stop=False, needs_client_attach=False)

    def commands(self, host: Host, probe: Probe) -> list[RemoteCmd]:
        be = _mux.get(host.mux, **host.mux_options)
        argv = be.reload_config()
        if argv is None:
            raise ActionUnavailable(f"{host.name}: {be.name} documents no config reload")
        return [RemoteCmd((be.name, *argv), f"{be.name} config reload (live, PTYs kept)", via="mux")]  # noqa: E501

    def verify(self, host: Host) -> Verify:
        be = _mux.get(host.mux, **host.mux_options)
        return Verify(f"{be.name} still answers", (RemoteCmd((be.name, *be.status_argv()), "status", mutating=False, via="mux"),))
