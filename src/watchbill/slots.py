"""Durable slot identity.

``slot_id`` is a ULID assigned once per occupant and stored in
``~/.local/share/watchbill/slots.json``. It is looked up by ``human_id``
(``host/session/workspace/tab/pane`` labels) first, then by the agent's
native ``agent_session`` id on the same host, and only then minted. Live
pane ids (``w3:p2``) never participate in the lookup: they are
generation-scoped in Herdr 0.8.2 and closed ids are never reused.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_ulid(now_ms: int | None = None, randomness: bytes | None = None) -> str:
    """26-char Crockford base32 ULID (48-bit ms timestamp + 80-bit random)."""
    ts = int(time.time() * 1000) if now_ms is None else now_ms
    rnd = os.urandom(10) if randomness is None else randomness
    if len(rnd) != 10:
        raise ValueError("ULID randomness must be 10 bytes")
    value = (ts << 80) | int.from_bytes(rnd, "big")
    out = []
    for _ in range(26):
        out.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(out))


@dataclass
class SlotRecord:
    slot_id: str
    human_id: str
    host: str
    agent_session: str | None = None
    kind: str | None = None
    first_seen: str = ""
    last_seen: str = ""


@dataclass
class SlotStore:
    """In-memory mirror of slots.json. ``save`` is the only write."""

    slots: dict[str, SlotRecord] = field(default_factory=dict)
    schema: int = 1

    # -- persistence -------------------------------------------------
    @classmethod
    def load(cls, path: Path) -> "SlotStore":
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text())
        store = cls(schema=raw.get("schema", 1))
        for sid, rec in raw.get("slots", {}).items():
            store.slots[sid] = SlotRecord(slot_id=sid, **{k: v for k, v in rec.items() if k != "slot_id"})
        return store

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"schema": self.schema, "slots": {
            sid: {k: v for k, v in rec.__dict__.items() if k != "slot_id"}
            for sid, rec in self.slots.items()}}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, path)

    # -- lookup ------------------------------------------------------
    def by_human_id(self, human_id: str) -> SlotRecord | None:
        for rec in self.slots.values():
            if rec.human_id == human_id:
                return rec
        return None

    def by_agent_session(self, host: str, agent_session: str | None) -> SlotRecord | None:
        if not agent_session:
            return None
        for rec in self.slots.values():
            if rec.host == host and rec.agent_session == agent_session:
                return rec
        return None

    def assign(self, *, human_id: str, host: str, agent_session: str | None = None,
               kind: str | None = None, now: str = "") -> str:
        """Return the durable slot_id for an occupant, minting one if needed.

        Match order: human_id, then (host, agent_session). A match by
        agent_session with a *different* human_id means the occupant moved
        (relabelled workspace, new tab); the slot follows the conversation and
        its human_id is updated.
        """
        rec = self.by_human_id(human_id) or self.by_agent_session(host, agent_session)
        if rec is None:
            rec = SlotRecord(slot_id=new_ulid(), human_id=human_id, host=host,
                             agent_session=agent_session, kind=kind, first_seen=now, last_seen=now)
            self.slots[rec.slot_id] = rec
            return rec.slot_id
        rec.human_id = human_id
        rec.host = host
        if agent_session:
            rec.agent_session = agent_session
        if kind:
            rec.kind = kind
        rec.last_seen = now or rec.last_seen
        return rec.slot_id
