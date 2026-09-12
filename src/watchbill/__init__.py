"""Watchbill: cockpit CLI that catalogs, parks, and restores coding-agent
fleets running inside Herdr 0.8.2 sessions.

Module contracts (see docs/architecture.md and CONTRIBUTING.md):

* planners (``plan_secure``, ``plan_set``, ``plan_relieve``) are pure;
* ``exec`` is the only module that mutates a live Herdr server;
* ``transport`` is the only package that knows SSH from local;
* ``actions`` declare blast radius and return commands, never run them.

Durable identity is ``slot_id`` (ULID) / ``human_id``; live pane ids such as
``w1:p2`` are per-generation hints only.
"""

__version__ = "0.0.1"

ROSTER_SCHEMA_VERSION = 1
HERDR_TARGET_VERSION = "0.8.2"
HERDR_TARGET_PROTOCOL = 20
# Namespaced by the GitHub owner it installs from. Not SFL-specific: the
# contract's original `sfl.watchbill` was overridden by the owner 2026-09-12.
PLUGIN_ID = "gw7523.watchbill"
