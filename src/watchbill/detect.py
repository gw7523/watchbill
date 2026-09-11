"""Agent status heuristics for muxes that do not know agents (tmux, cmux).

Herdr reports idle/working/blocked itself. tmux gives us the foreground
command name, seconds since last output, and the screen. From those:

* ``blocked``  — the last screen lines match a known approval/question
  pattern for the agent kind (heuristic; patterns below).
* ``idle``     — quiet for at least ``idle_after_s`` and no pattern matched.
* ``working``  — output within ``idle_after_s``.
* ``unknown``  — nothing to go on (no screen, no activity age). ``secure``
  treats ``unknown`` like ``working``: no park without ``--force``.
"""
from __future__ import annotations

import re

# Lines that mean "an approval or question dialog is up". Conservative:
# a false 'blocked' only costs a refusal; a false 'idle' would type /exit
# into a dialog.
PROMPT_PATTERNS: dict[str, tuple[str, ...]] = {
    "claude": (r"Do you want to", r"❯\s*1\.\s*Yes", r"Yes, and don't ask again", r"\(esc to cancel\)", r"\[y/n\]"),
    "codex": (r"Allow", r"Approve", r"\[y/N\]", r"press y to", r"Update available"),
    "grok": (r"Approve", r"Allow", r"\[y/n\]", r"Do you want to"),
    "gemini": (r"Allow", r"\(y/n\)", r"Do you want to"),
    "cursor": (r"Allow", r"Run command\?", r"\[y/n\]"),
    "opencode": (r"Allow", r"permission", r"\[y/n\]"),
    "*": (r"\[y/n\]", r"\(y/n\)", r"\[Y/n\]", r"\[y/N\]", r"Press Enter to continue"),
}


def looks_blocked(kind: str | None, screen: str | None, tail_lines: int = 12) -> bool:
    if not screen:
        return False
    tail = "\n".join([ln for ln in screen.splitlines() if ln.strip()][-tail_lines:])
    pats = PROMPT_PATTERNS.get(kind or "", ()) + PROMPT_PATTERNS["*"]
    return any(re.search(p, tail) for p in pats)


def agent_status(kind: str | None, *, activity_age_s: float | None, screen: str | None,
                 idle_after_s: float = 30.0) -> str:
    if looks_blocked(kind, screen):
        return "blocked"
    if activity_age_s is None:
        return "unknown"
    return "idle" if activity_age_s >= idle_after_s else "working"


def status_is_parkable(status: str | None) -> bool:
    """Only a status we can trust as 'at its prompt' is parkable without --force."""
    return status in ("idle", "done")
