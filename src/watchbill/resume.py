"""Native agent resume argv (Herdr 0.8.2 integrations).

Every row carries a ``verify`` note. Rows marked ``UNVERIFIED-0.8.2`` must be
probed with ``<bin> --help`` on a live box before MVP; ``plan_set`` still
emits them but flags the step. See docs/herdr-0.8.2-facts.md.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ResumeSpec:
    kind: str
    argv: tuple[str, ...]              # "{id}" is replaced with agent_session.value
    fallback: tuple[str, ...] | None   # continue-most-recent variant, if any
    verify: str
    verified: bool


RESUME: dict[str, ResumeSpec] = {
    "claude": ResumeSpec("claude", ("claude", "--resume", "{id}"), ("claude", "--continue"),
                         "Claude Code 2.1.267 --help: --resume <session-id>", True),
    "grok": ResumeSpec("grok", ("grok", "--resume", "{id}"), ("grok", "--continue"),
                       "grok 1.0.25 --help: -r, --resume [<SESSION_ID_OR_TITLE>]", True),
    "codex": ResumeSpec("codex", ("codex", "resume", "{id}"), ("codex", "resume", "--last"),
                        "codex-cli 0.153.4 resume --help: codex resume [SESSION_ID]", True),
    "cursor": ResumeSpec("cursor", ("cursor-agent", "--resume", "{id}"), ("cursor-agent", "--continue"),
                         "cursor-agent 2026.09.10 --help: --resume [chatId]", True),
    "opencode": ResumeSpec("opencode", ("opencode", "--session", "{id}"), ("opencode", "--continue"),
                           "opencode 1.18.29 --help: -s, --session <id>", True),
    "hermes": ResumeSpec("hermes", ("hermes", "--resume", "{id}"), None,
                         "UNVERIFIED-0.8.2: contract cites official integration; probe hermes --help", False),
    "gemini": ResumeSpec("gemini", ("gemini", "--resume", "{id}"), ("gemini", "--resume", "latest"),
                         "gemini 0.59.0 --help: -r, --resume (id or index); not in contract table", True),
}


def resume_argv(kind: str | None, session_id: str | None) -> list[str] | None:
    """Argv to hand to ``herdr agent start … -- <argv>``. None when the kind
    has no known resume path or there is no session id (unref)."""
    if not kind or kind not in RESUME or not session_id:
        return None
    return [tok.replace("{id}", session_id) for tok in RESUME[kind].argv]


def is_verified(kind: str | None) -> bool:
    return bool(kind and kind in RESUME and RESUME[kind].verified)
