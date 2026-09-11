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
        if host.mux == "cmux":
            raise ActionUnavailable(f"{host.name}: cmux documents no plugin system")
        if host.mux == "tmux":
            # TPM is the de-facto manager; live (source-file), nothing parked. Refuses if TPM is absent.
            spec = self.options.get("plugin")
            tpm = "$HOME/.tmux/plugins/tpm/bin/install_plugins"
            cmds = [RemoteCmd(("sh", "-c", f'test -x "{tpm}" || {{ echo "TPM not installed" >&2; exit 3; }}'),
                              "check TPM is installed", mutating=False),
                    RemoteCmd(("sh", "-c", f'"{tpm}"'), f"TPM install plugins ({spec or 'from ~/.tmux.conf'})", unverified=True),
                    RemoteCmd(("tmux", "source-file", "~/.tmux.conf"), "tmux source-file (live reload)", via="mux")]
            return cmds
        spec = self.options.get("plugin")
        if not spec:
            raise ActionUnavailable("install-plugin needs --plugin owner/repo[/subdir]")
        argv = [host.herdr_bin, "plugin", "install", spec, "--yes"]
        if self.options.get("ref"):
            argv += ["--ref", str(self.options["ref"])]
        # bare `herdr plugin install`: no --session, no running server needed;
        # before_stop so a hooks-bounce installs first, then stops, then restores.
        return [RemoteCmd(tuple(argv), f"install plugin {spec}", via="shell", before_stop=True)]

    def verify(self, host: Host) -> Verify:
        return Verify("plugin listed", (RemoteCmd((host.herdr_bin, "plugin", "list"), "plugin list", mutating=False, via="shell"),))
