"""Resume prompts: pinned > role template > excerpt-drafted > none.

* ``~/.config/watchbill/pins.toml``::

      [pins."rig2/default/personal-config/1/p1"]   # human_id or slot_id
      prompt = "Resume the btop pin work; run --check before apply."

* ``~/.config/watchbill/prompts/<role>.txt`` — a template per role with
  ``{human_id}``, ``{cwd}``, ``{tasking}``, ``{kind}`` placeholders.
* excerpt-drafted: the last non-empty lines of the pane, wrapped in a
  fixed frame. Excerpts are cockpit-local (``snap.excerpt_sync = false``).

``resolve`` is pure; the CLI decides what to read from disk.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field

from .roster import ResumePrompt

EXCERPT_FRAME = ("You were parked by Watchbill during a maintenance window. Your last visible "
                 "output is below. Pick up where you left off; do not repeat completed steps.\n\n---\n{excerpt}\n---")


@dataclass
class Pins:
    by_key: dict[str, str] = field(default_factory=dict)

    @classmethod
    def parse(cls, text: str) -> "Pins":
        raw = tomllib.loads(text)
        out = {}
        for key, val in (raw.get("pins") or {}).items():
            if isinstance(val, dict) and "prompt" in val:
                out[key] = str(val["prompt"])
            elif isinstance(val, str):
                out[key] = val
        return cls(out)

    def get(self, *keys: str | None) -> str | None:
        for k in keys:
            if k and k in self.by_key:
                return self.by_key[k]
        return None


def resolve(*, human_id: str, slot_id: str | None, role: str, kind: str | None, cwd: str,
            tasking: str | None, pins: Pins | None, templates: dict[str, str] | None,
            excerpt: str | None, excerpt_lines: int = 12) -> ResumePrompt:
    pinned = pins.get(slot_id, human_id) if pins else None
    if pinned:
        return ResumePrompt("pinned", pinned)
    tpl = (templates or {}).get(role)
    if tpl:
        return ResumePrompt("role", tpl.format(human_id=human_id, cwd=cwd, tasking=tasking or "", kind=kind or ""))
    if excerpt:
        lines = [ln.rstrip() for ln in excerpt.splitlines() if ln.strip()]
        if lines:
            return ResumePrompt("excerpt", EXCERPT_FRAME.format(excerpt="\n".join(lines[-excerpt_lines:])))
    return ResumePrompt("none", "")
