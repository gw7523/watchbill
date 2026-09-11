"""Maintenance-window actions. ``get(name)`` → class; ``REGISTRY`` is the
built-in table. Adding "upgrade Claude CLI" or "install plugin X" means a
new module here, never a new top-level verb.
"""
from __future__ import annotations

from .base import Action, ActionContext, ActionUnavailable, BaseAction, BlastRadius, RemoteCmd, Verify
from .custom import Custom
from .install_plugin import InstallPlugin
from .omarchy_update import OmarchyUpdate
from .restart_harness import RestartHarness
from .restart_herdr import RestartHerdr
from .upgrade_agents import UpgradeAgents
from .upgrade_herdr import UpgradeHerdr

REGISTRY: dict[str, type[BaseAction]] = {
    UpgradeHerdr.name: UpgradeHerdr,
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


__all__ = ["Action", "ActionContext", "ActionUnavailable", "BaseAction", "BlastRadius", "RemoteCmd", "Verify",
           "REGISTRY", "get", "blast_radius_table"]
