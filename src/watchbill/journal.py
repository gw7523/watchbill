"""Append-only JSONL journal at ``~/.local/state/watchbill/journal.jsonl``.

One line per step outcome. ``relieve --resume`` reads the last run for the
same verb/action and skips hosts already marked ``set-complete``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .roster import now_iso

STATUSES = ("planned", "dry-run", "ok", "fail", "skipped", "refused", "set-complete", "set-partial", "host-start", "host-done")


@dataclass
class Journal:
    path: Path

    def append(self, *, run_id: str, verb: str, host: str | None, step_id: str | None,
               status: str, detail: str = "", **extra) -> None:
        if status not in STATUSES:
            raise ValueError(f"unknown journal status {status!r}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        rec = {"ts": now_iso(), "run_id": run_id, "verb": verb, "host": host,
               "step": step_id, "status": status, "detail": detail, **extra}
        with self.path.open("a") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")

    def entries(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for ln in self.path.read_text().splitlines():
            ln = ln.strip()
            if ln:
                out.append(json.loads(ln))
        return out

    def last_run_id(self, verb: str) -> str | None:
        for rec in reversed(self.entries()):
            if rec.get("verb") == verb:
                return rec.get("run_id")
        return None

    def hosts_marked(self, run_id: str, status: str = "set-complete") -> set[str]:
        return {r["host"] for r in self.entries()
                if r.get("run_id") == run_id and r.get("status") == status and r.get("host")}
