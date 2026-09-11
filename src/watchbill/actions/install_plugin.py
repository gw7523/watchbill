"""install-plugin: ``herdr plugin install owner/repo --yes``.

Does not need a running server. ``[[startup]]`` hooks in the plugin manifest
run after session restore / live handoff, **not** on link/enable — so when
the manifest declares startup hooks the blast radius requests a session stop
(agents parked, session bounced, hooks fire on restore). ``enable`` alone
never means "hook fired".

Whether the manifest has startup hooks is passed in as
``options["startup_hooks"]`` (read by the CLI from the fetched manifest, or
``--startup-hooks`` given by the operator); default False.
"""
from __future__ import annotations

from ..doctor import Probe
from ..hosts import Host
from .base import ActionUnavailable, BaseAction, BlastRadius, RemoteCmd, Verify


class InstallPlugin(BaseAction):
    name = "install-plugin"

    def blast_radius(self) -> BlastRadius:
        hooks = bool(self.options.get("startup_hooks", False))
        return BlastRadius(park_roles=frozenset({"agent"}) if hooks else frozenset(),
                           needs_session_stop=hooks, needs_client_attach=hooks)

    def commands(self, host: Host, probe: Probe) -> list[RemoteCmd]:
        spec = self.options.get("plugin")
        if not spec:
            raise ActionUnavailable("install-plugin needs --plugin owner/repo[/subdir]")
        argv = [host.herdr_bin, "plugin", "install", spec, "--yes"]
        if self.options.get("ref"):
            argv += ["--ref", str(self.options["ref"])]
        return [RemoteCmd(tuple(argv), f"install plugin {spec}", via="herdr")]

    def verify(self, host: Host) -> Verify:
        return Verify("plugin listed", (RemoteCmd((host.herdr_bin, "plugin", "list"), "plugin list", mutating=False, via="herdr"),))
