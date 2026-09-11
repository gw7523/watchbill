"""upgrade-agents: bump claude / grok / codex / … CLIs. Session stays up.

Blast radius parks only agents of the requested kinds (``--kinds``, default:
every kind present in the roster) and does **not** stop the session. After
the bump, ``set`` restarts those slots on the new binary with their native
``--resume`` argv, and ``herdr integration install <kind>`` refreshes the
integration hook if it ships separately.

Per-kind upgrade command by install flavor (from the doctor probe):

| flavor | command |
|---|---|
| mise | ``mise upgrade <tool>`` |
| npm | ``npm install -g <pkg>@latest`` |
| brew | ``brew upgrade <formula>`` |
| unknown | the CLI's own self-update if it has one, else refused |
"""
from __future__ import annotations

from ..doctor import Probe
from ..hosts import Host
from .base import ActionUnavailable, BaseAction, BlastRadius, RemoteCmd

MISE_TOOL = {"claude": "claude", "codex": "codex", "cursor": "cursor-agent", "gemini": "gemini",
             "opencode": "opencode", "grok": "npm:@xai-official/grok", "hermes": "hermes"}
NPM_PKG = {"claude": "@anthropic-ai/claude-code", "codex": "@openai/codex", "grok": "@xai-official/grok",
           "opencode": "opencode-ai", "gemini": "@google/gemini-cli"}
BREW_FORMULA = {"claude": "claude-code", "codex": "codex", "opencode": "opencode", "gemini": "gemini-cli"}
# Self-update subcommands. Marked unverified where not read from --help on a live box.
SELF_UPDATE = {"claude": (("claude", "update"), False), "cursor": (("cursor-agent", "update"), True),
               "opencode": (("opencode", "upgrade"), True), "grok": (("grok", "upgrade"), True)}


class UpgradeAgents(BaseAction):
    name = "upgrade-agents"

    def kinds(self) -> tuple[str, ...]:
        ks = self.options.get("kinds")
        return tuple(ks) if ks else ()

    def blast_radius(self) -> BlastRadius:
        ks = self.kinds()
        if not ks:
            # no --kinds: park only kinds a probe can actually upgrade (union over hosts)
            ks = tuple(sorted({k for p in self.ctx.probes.values() for k in p.agent_versions}))
        return BlastRadius(park_roles=frozenset({"agent"}), needs_session_stop=False, needs_client_attach=True,
                           park_kinds=frozenset(ks))

    def park_kinds_for(self, host: Host, probe: Probe) -> frozenset[str] | None:
        return frozenset(self.kinds() or tuple(sorted(probe.agent_versions)))

    def commands(self, host: Host, probe: Probe) -> list[RemoteCmd]:
        kinds = self.kinds() or tuple(sorted(probe.agent_versions))
        if not kinds:
            raise ActionUnavailable(f"{host.name}: no agent kinds to upgrade (pass --kinds or roll first)")
        out: list[RemoteCmd] = []
        for kind in kinds:
            flavor = probe.agent_flavors.get(kind, "unknown")
            if flavor == "mise" and kind in MISE_TOOL:
                out.append(RemoteCmd(("mise", "upgrade", MISE_TOOL[kind]), f"mise upgrade {kind}"))
            elif flavor == "npm" and kind in NPM_PKG:
                out.append(RemoteCmd(("npm", "install", "-g", f"{NPM_PKG[kind]}@latest"), f"npm upgrade {kind}"))
            elif flavor == "brew" and kind in BREW_FORMULA:
                out.append(RemoteCmd(("brew", "upgrade", BREW_FORMULA[kind]), f"brew upgrade {kind}"))
            elif kind in SELF_UPDATE:
                argv, unverified = SELF_UPDATE[kind]
                out.append(RemoteCmd(argv, f"{kind} self-update", unverified=unverified))
            else:
                raise ActionUnavailable(f"{host.name}: no upgrade path for agent kind {kind!r} (flavor={flavor})")
            if host.mux == "herdr":
                # Only Herdr ships per-agent integration hooks; tmux and cmux
                # have no equivalent, so there is nothing to refresh there.
                out.append(RemoteCmd((host.herdr_bin, "integration", "install", kind),
                                     f"refresh herdr {kind} integration hook", via="mux"))
        return out
