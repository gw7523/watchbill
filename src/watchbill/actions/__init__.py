"""Maintenance-window actions. ``get(name)`` → class; ``REGISTRY`` is the
built-in table. Adding "upgrade Claude CLI" or "install plugin X" means a
new module here, never a new top-level verb.
"""
from __future__ import annotations

from .base import Action, ActionContext, ActionUnavailable, BaseAction, BlastRadius, RemoteCmd, Verify
from .custom import Custom
from .install_plugin import InstallPlugin
from .omarchy_update import OmarchyUpdate
from .reload_config import ReloadConfig
from .restart_harness import RestartHarness
from .restart_herdr import RestartHerdr
from .upgrade_agents import UpgradeAgents
from .upgrade_herdr import UpgradeHerdr, UpgradeMux

REGISTRY: dict[str, type[BaseAction]] = {
    UpgradeMux.name: UpgradeMux,
    "upgrade-herdr": UpgradeHerdr,          # contract name; same action
    ReloadConfig.name: ReloadConfig,
    RestartHerdr.name: RestartHerdr,
    OmarchyUpdate.name: OmarchyUpdate,
    UpgradeAgents.name: UpgradeAgents,
    InstallPlugin.name: InstallPlugin,
    RestartHarness.name: RestartHarness,
    Custom.name: Custom,
}


def get(name: str, ctx: ActionContext | None = None) -> BaseAction:
    try:
        cls = REGISTRY[name]
    except KeyError:
        raise ActionUnavailable(f"unknown action {name!r}; known: {', '.join(REGISTRY)}") from None
    return cls(ctx)


def blast_radius_table(ctx: ActionContext | None = None) -> dict[str, BlastRadius]:
    return {name: cls(ctx).blast_radius() for name, cls in REGISTRY.items()}


def per_mux_matrix() -> dict[str, dict[str, str]]:
    """Which actions each mux supports; docs + tests read this."""
    return {
        "upgrade-mux": {"herdr": "cold (flavor)", "tmux": "cold (brew/pacman)", "cmux": "cold (brew cask, UNVERIFIED) + quit/relaunch"},
        "restart-herdr": {"herdr": "cold", "tmux": "cold (kill-server)", "cmux": "cold (quit app, open -a cmux; app resumes agents)"},
        "restart-harness": {"herdr": "cold", "tmux": "cold (kill-server)", "cmux": "cold (quit app, open -a cmux; app resumes agents)"},
        "reload-config": {"herdr": "live", "tmux": "live (source-file)", "cmux": "live (reload-config)"},
        "install-plugin": {"herdr": "live, bounce if startup hooks", "tmux": "live (TPM + source-file)", "cmux": "refused (no plugin system)"},
        "upgrade-agents": {"herdr": "no stop", "tmux": "no stop", "cmux": "no stop"},
        "omarchy-update": {"herdr": "pacman hosts", "tmux": "pacman hosts", "cmux": "refused"},
        "custom": {"herdr": "declared", "tmux": "declared", "cmux": "declared"},
    }


__all__ = ["Action", "ActionContext", "ActionUnavailable", "BaseAction", "BlastRadius", "RemoteCmd", "Verify",
           "REGISTRY", "get", "blast_radius_table"]
