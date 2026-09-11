"""custom: operator-declared command with operator-declared blast radius.

    watchbill relieve custom --cmd 'sudo systemctl restart tailscaled' \
        --park agent,watcher --session-stop --yes

Nothing is inferred. ``--may-reboot`` declares that the command can reboot
the host; the operator's ``--allow-reboot`` is still required separately and
is never honoured on the cockpit.
"""
from __future__ import annotations

import shlex

from ..classify import roles_matching
from ..doctor import Probe
from ..hosts import Host
from .base import ActionUnavailable, BaseAction, BlastRadius, RemoteCmd


class Custom(BaseAction):
    name = "custom"

    def blast_radius(self) -> BlastRadius:
        park = self.options.get("park") or ()
        if isinstance(park, str):
            park = [p for p in park.split(",") if p]
        return BlastRadius(park_roles=roles_matching(park), needs_session_stop=bool(self.options.get("session_stop")),
                           needs_client_attach=bool(self.options.get("session_stop")),
                           allow_reboot=bool(self.options.get("may_reboot")))

    def commands(self, host: Host, probe: Probe) -> list[RemoteCmd]:
        cmd = self.options.get("cmd")
        if not cmd:
            raise ActionUnavailable("custom needs --cmd")
        argv = tuple(shlex.split(cmd)) if isinstance(cmd, str) else tuple(cmd)
        return [RemoteCmd(argv, "custom command")]
