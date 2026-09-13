"""Roster schema 1: load / save / merge / guard.

A roster is one fleet-wide catalog taken at one instant. Occupants are a flat
list keyed by ``slot_id`` / ``human_id``; per-session shapes (workspace →
tab → pane, labels only, no commands) are stored beside them so ``set`` can
rebuild layout by label. ``current.json`` is a symlink to the last *good*
roster; :func:`occupant_guard` decides whether a new roster may take it over.

JSON Schema: ``docs/roster.schema.json`` (validated in tests with
jsonschema, a dev dependency; the runtime does not validate).
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import ROSTER_SCHEMA_VERSION

REASONS = ("manual", "heartbeat", "pre-relieve", "post-set", "pre-secure", "post-secure")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class LiveIds:
    """Hints for this server generation only. Never a plan key."""
    workspace_id: str | None = None
    tab_id: str | None = None
    pane_id: str | None = None
    terminal_id: str | None = None


@dataclass
class ResumePrompt:
    source: str = "none"   # pinned | role | excerpt | none
    text: str = ""


@dataclass
class Occupant:
    slot_id: str
    human_id: str
    host: str
    session: str
    workspace_label: str
    tab_label: str
    pane_label: str
    role: str
    kind: str | None = None
    agent_status: str | None = None
    argv: list[str] = field(default_factory=list)
    cmdline: str = ""
    cwd: str = ""
    foreground_cwd: str | None = None
    agent_session: dict | None = None
    resume_argv: list[str] | None = None
    resume_prompt: ResumePrompt = field(default_factory=ResumePrompt)
    allow_relaunch: bool = False
    live_ids: LiveIds = field(default_factory=LiveIds)
    tasking: str | None = None
    excerpt: str | None = None   # cockpit-local unless snap.excerpt_sync
    taken_at: str = ""
    mux: str = "herdr"           # which multiplexer owns this pane (additive, schema 1)
    # Launch configuration recorded while the agent ran: flags, permission
    # mode, model, seat env (allowlisted, no secrets), and an on-disk
    # fingerprint (settings/plugins/hooks/MCP/trust/version/integration).
    # See agentconfig.py. Additive, schema 1.
    agent_config: dict | None = None
    # Matched a host `exclude` glob (the glob itself). Catalogued, never touched.
    excluded: str | None = None

    @property
    def effective_cwd(self) -> str:
        """Pitfall 4: prefer foreground_cwd over pane cwd when present."""
        return self.foreground_cwd or self.cwd

    @property
    def unref(self) -> bool:
        return self.role == "agent" and not (self.agent_session and self.agent_session.get("value"))


@dataclass
class HostRecord:
    host: str
    reachable: bool
    herdr: dict = field(default_factory=dict)   # version, protocol, flavor, socket, live_handoff
    error: str | None = None
    cockpit: bool = False
    mux: str = "herdr"


@dataclass
class Shape:
    """Label-keyed layout of one session. ``splits`` is Herdr's own layout
    tree per tab, copied verbatim so a socket transport can feed it back to
    ``layout.apply`` with commands stripped; ``ssh_cli`` ignores it and uses
    ``pane split`` directions derived from rects."""
    host: str
    session: str
    workspaces: list[dict] = field(default_factory=list)
    mux: str = "herdr"


@dataclass
class Roster:
    fleet: str
    taken_at: str
    reason: str
    cockpit: dict = field(default_factory=dict)  # host, pane_id, session
    hosts: list[HostRecord] = field(default_factory=list)
    shapes: list[Shape] = field(default_factory=list)
    occupants: list[Occupant] = field(default_factory=list)
    schema: int = ROSTER_SCHEMA_VERSION

    # -- queries -----------------------------------------------------
    def by_slot(self, slot_id: str) -> Occupant | None:
        return next((o for o in self.occupants if o.slot_id == slot_id), None)

    def by_human(self, human_id: str) -> Occupant | None:
        return next((o for o in self.occupants if o.human_id == human_id), None)

    def on_host(self, host: str) -> list[Occupant]:
        return [o for o in self.occupants if o.host == host]

    def in_session(self, host: str, session: str) -> list[Occupant]:
        return [o for o in self.occupants if o.host == host and o.session == session]

    def shape_for(self, host: str, session: str) -> Shape | None:
        return next((s for s in self.shapes if s.host == host and s.session == session), None)

    def host_record(self, host: str) -> HostRecord | None:
        return next((h for h in self.hosts if h.host == host), None)

    def resolve(self, target: str, *, host: str | None = None) -> list[Occupant]:
        """Resolve an operator target: slot_id, full human_id, or a label
        suffix (``workspace/tab/pane`` or just ``workspace``). A suffix that
        matches on more than one host is a collision (pitfall 11) — the caller
        must pass ``--host`` or refuse."""
        exact = [o for o in self.occupants if o.slot_id == target or o.human_id == target]
        if exact:
            return exact
        hits = [o for o in self.occupants
                if (o.human_id.endswith("/" + target) or o.workspace_label == target)
                and (host is None or o.host == host)]
        return hits

    # -- serialisation -----------------------------------------------
    def to_dict(self) -> dict:
        d = asdict(self)
        # asdict puts `schema` last; the schema wants it first for humans.
        return {"schema": d.pop("schema"), **d}

    @classmethod
    def from_dict(cls, d: dict) -> "Roster":
        if d.get("schema") != ROSTER_SCHEMA_VERSION:
            raise ValueError(f"roster schema {d.get('schema')!r} != {ROSTER_SCHEMA_VERSION}")
        occ = []
        for o in d.get("occupants", []):
            o = dict(o)
            o["resume_prompt"] = ResumePrompt(**(o.get("resume_prompt") or {}))
            o["live_ids"] = LiveIds(**(o.get("live_ids") or {}))
            occ.append(Occupant(**o))
        return cls(
            fleet=d["fleet"], taken_at=d["taken_at"], reason=d["reason"], cockpit=d.get("cockpit", {}),
            hosts=[HostRecord(**h) for h in d.get("hosts", [])],
            shapes=[Shape(**s) for s in d.get("shapes", [])],
            occupants=occ, schema=d["schema"],
        )


def load(path: Path) -> Roster:
    return Roster.from_dict(json.loads(path.read_text()))


def save(roster: Roster, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(roster.to_dict(), indent=2) + "\n")
    os.replace(tmp, path)
    return path


def roster_filename(roster: Roster) -> str:
    stamp = roster.taken_at.replace(":", "").replace("-", "").replace("+0000", "Z")
    return f"{stamp}-{roster.reason}.json"


# -- merge -------------------------------------------------------------

def merge(current: Roster, previous: Roster | None) -> Roster:
    """Carry operator-owned fields forward from the previous roster.

    ``resume_prompt`` (when pinned) and ``allow_relaunch`` overrides survive a
    re-roll; everything observed live (role, argv, live_ids, status) is taken
    from ``current``. Matching is by slot_id.
    """
    if previous is None:
        return current
    prev = {o.slot_id: o for o in previous.occupants}
    for occ in current.occupants:
        p = prev.get(occ.slot_id)
        if not p:
            continue
        if occ.resume_prompt.source == "none" and p.resume_prompt.source == "pinned":
            occ.resume_prompt = p.resume_prompt
        if p.allow_relaunch and not occ.allow_relaunch and occ.role == p.role:
            occ.allow_relaunch = True
    return current


# -- guard -------------------------------------------------------------

@dataclass(frozen=True)
class GuardConfig:
    drop_ratio: float = 0.5
    min_occupants: int = 2


@dataclass(frozen=True)
class GuardResult:
    ok: bool
    previous: int
    current: int
    reason: str = ""


def occupant_guard(previous: int, current: int, cfg: GuardConfig = GuardConfig(), *, force: bool = False) -> GuardResult:
    """Pitfall 8 (#3415): a reboot can leave an empty ``session.json`` and a
    heartbeat snap would then record an empty fleet as *the* fleet. Refuse to
    retarget ``current.json`` when the occupant count drops by ``drop_ratio``
    or more, or below ``min_occupants`` (only enforced once the previous good
    roster had at least that many), unless ``--force``.
    """
    if force:
        return GuardResult(True, previous, current, "forced")
    if previous <= 0:
        return GuardResult(True, previous, current, "no previous roster")
    if current / previous <= 1 - cfg.drop_ratio:
        return GuardResult(False, previous, current,
                           f"occupants dropped {previous}→{current} (≥{int(cfg.drop_ratio * 100)}%)")
    if previous >= cfg.min_occupants and current < cfg.min_occupants:
        return GuardResult(False, previous, current,
                           f"occupants {current} < min_occupants {cfg.min_occupants}")
    return GuardResult(True, previous, current, "ok")


def current_count(fleet_dir: Path) -> int:
    cur = fleet_dir / "current.json"
    if not cur.exists():
        return 0
    try:
        return len(json.loads(cur.read_text()).get("occupants", []))
    except (OSError, ValueError):
        return 0


def write(roster: Roster, fleet_dir: Path, cfg: GuardConfig = GuardConfig(), *, force: bool = False) -> tuple[Path, GuardResult]:
    """Write the timestamped roster; retarget ``current.json`` only if the
    guard passes. The timestamped file is always kept so nothing is lost."""
    path = save(roster, fleet_dir / roster_filename(roster))
    result = occupant_guard(current_count(fleet_dir), len(roster.occupants), cfg, force=force)
    if result.ok:
        link = fleet_dir / "current.json"
        tmp = fleet_dir / "current.json.tmp"
        if tmp.is_symlink() or tmp.exists():
            tmp.unlink()
        os.symlink(path.name, tmp)
        os.replace(tmp, link)
    return path, result
