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
        # Only Herdr has `[[startup]]` hooks that fire on session restore. A
        # tmux plugin is loaded by `source-file`, which keeps every PTY, so a
        # tmux plugin install never bounces the server (which would be
        # `kill-server` — the whole fleet on that host).
        hooks = bool(self.options.get("startup_hooks", False)) and self.options.get("mux", "herdr") == "herdr"
        return BlastRadius(park_roles=frozenset({"agent"}) if hooks else frozenset(),
                           needs_session_stop=hooks, needs_client_attach=hooks)

    def commands(self, host: Host, probe: Probe) -> list[RemoteCmd]:
        if host.mux == "cmux":
            raise ActionUnavailable(f"{host.name}: cmux documents no plugin system")
        if host.mux == "tmux":
            # TPM is the de-facto manager; live (source-file), nothing parked.
            # TPM shells out to plain `tmux`, which would talk to the DEFAULT
            # socket — not the server Watchbill manages. Pin it with -L via
            # a wrapper on PATH so the install lands on the right server.
            spec = self.options.get("plugin")
            sock = self.options.get("session") or "default"
            tpm = "$HOME/.tmux/plugins/tpm/bin/install_plugins"
            shim = (f'd=$(mktemp -d); printf \'#!/bin/sh\\nexec /usr/bin/env tmux -L %s "$@"\\n\' {sock} > "$d/tmux"; '
                    f'chmod +x "$d/tmux"; PATH="$d:$PATH" "{tpm}"; rc=$?; rm -rf "$d"; exit $rc')
            return [RemoteCmd(("sh", "-c", f'test -x "{tpm}" || {{ echo "TPM not installed at {tpm}" >&2; exit 3; }}'),
                              "check TPM is installed", mutating=False),
                    RemoteCmd(("sh", "-c", shim), f"TPM install plugins on socket {sock} ({spec or 'from ~/.tmux.conf'})",
                              unverified=True),
                    RemoteCmd(("tmux", "source-file", "~/.tmux.conf"), "tmux source-file (live reload, PTYs kept)", via="mux")]
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
        if host.mux == "tmux":
            return Verify("plugin dir present after TPM",
                          (RemoteCmd(("sh", "-c", "ls $HOME/.tmux/plugins"), "list tmux plugins", mutating=False),))
        return Verify("plugin listed", (RemoteCmd((host.herdr_bin, "plugin", "list"), "plugin list", mutating=False, via="shell"),))
