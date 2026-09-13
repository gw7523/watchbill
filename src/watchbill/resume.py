"""Native agent resume argv (Herdr 0.8.2 integrations).

Every row carries a ``verify`` note. Rows marked ``UNVERIFIED-0.8.2`` must be
probed with ``<bin> --help`` on a live box before MVP; ``plan_set`` still
emits them but flags the step. See docs/herdr-0.8.2-facts.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


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


# Flags that take a value, per kind, read from `<bin> --help` on 2026-09-13
# (claude 2.1.267, grok 1.0.25). Only kinds with a verified table get their
# original flags carried into a resume; the rest resume with the bare form.
VALUE_FLAGS: dict[str, frozenset[str]] = {
    "claude": frozenset({
        "--add-dir", "--agent", "--agents", "--append-system-prompt", "--autocompact", "--betas",
        "--debug-file", "--effort", "--environment", "--fallback-model", "--file", "--input-format",
        "--json-schema", "--max-budget-usd", "--mcp-config", "--model", "-n", "--name", "--output-format",
        "--permission-mode", "--permission-prompts", "--plugin-dir", "--plugin-url",
        "--remote-control-session-name-prefix", "--setting-sources", "--settings", "--system-prompt",
        "--system-prompt-snapshot", "--tools"}),
    "grok": frozenset({
        "--agent", "--agents", "--allow", "--cwd", "--debug-file", "--deny", "--disallowed-tools",
        "--json-schema", "--leader-socket", "-m", "--model", "--max-turns", "--output-format",
        "--permission-mode", "--reasoning-effort", "--rules", "--sandbox", "--system-prompt-override",
        "--tools", "--worktree-ref"}),
}
# Flags whose value is optional ("[value]" in --help) and which are kept.
OPTIONAL_VALUE_FLAGS: dict[str, frozenset[str]] = {
    "claude": frozenset({"-d", "--debug", "-w", "--worktree", "--prompt-suggestions", "--remote-control"}),
    "grok": frozenset({"-w", "--worktree"}),
}
# Flags that must NOT survive into a resume: an earlier resume/continue
# selection, a one-shot prompt, or a new session id (which would fork the
# conversation instead of resuming it). req = always takes a value,
# opt = value only if the next token is not a flag, none = boolean.
DROP_FLAGS: dict[str, dict[str, str]] = {
    "claude": {"-r": "opt", "--resume": "opt", "-c": "none", "--continue": "none", "--session-id": "req",
               "-p": "none", "--print": "none", "--from-pr": "opt", "--teleport": "opt", "--cloud": "opt",
               "--fork-session": "none"},
    "grok": {"-r": "opt", "--resume": "opt", "-c": "none", "--continue": "none", "-s": "req",
             "--session-id": "req", "-p": "req", "--single": "req", "--prompt-file": "req", "--prompt-json": "req"},
}


def carried_flags(kind: str | None, original_argv: Sequence[str] | None) -> list[str]:
    """The agent's own flags from the argv it was running with, minus anything
    that selects a conversation or sends a prompt.

    Why this matters: the owner's agents run as `claude
    --dangerously-skip-permissions`. Resuming them as a bare `claude --resume
    <id>` would silently bring them back in a different permission mode.

    Positional arguments (an initial prompt) are never carried, so a resume can
    never re-send the task the agent was started with. A flag missing from the
    tables is treated as boolean: if it really takes a value, the value is
    dropped and the agent fails to start loudly rather than doing the wrong
    thing quietly.
    """
    if not kind or kind not in VALUE_FLAGS or not original_argv:
        return []
    from .classify import AGENT_KINDS
    import os
    exe = AGENT_KINDS.get(kind, kind)
    at = next((i for i, t in enumerate(original_argv) if os.path.basename(t) == exe), None)
    if at is None:
        return []
    toks = list(original_argv[at + 1:])
    out: list[str] = []
    j = 0
    while j < len(toks):
        t = toks[j]
        if not t.startswith("-"):
            j += 1                                  # positional: an initial prompt, never replayed
            continue
        name, eq, _ = t.partition("=")
        drop = DROP_FLAGS.get(kind, {}).get(name)
        j += 1
        if drop:
            if not eq and drop == "req" and j < len(toks):
                j += 1
            elif not eq and drop == "opt" and j < len(toks) and not toks[j].startswith("-"):
                j += 1
            continue
        out.append(t)
        takes = name in VALUE_FLAGS[kind] or name in OPTIONAL_VALUE_FLAGS.get(kind, ())
        if takes and not eq and j < len(toks) and not toks[j].startswith("-"):
            out.append(toks[j])
            j += 1
    return out


def resume_argv(kind: str | None, session_id: str | None, original_argv: Sequence[str] | None = None) -> list[str] | None:
    """Argv to hand to ``herdr agent start … -- <argv>``. None when the kind
    has no known resume path or there is no session id (unref). The agent's
    own flags are carried over for kinds with a verified flag table."""
    if not kind or kind not in RESUME or not session_id:
        return None
    base = [tok.replace("{id}", session_id) for tok in RESUME[kind].argv]
    return [base[0], *carried_flags(kind, original_argv), *base[1:]]


def continue_argv(kind: str | None, original_argv: Sequence[str] | None = None) -> list[str] | None:
    """cwd-scoped 'continue the most recent conversation here' form, for
    muxes without a native session id (tmux, cmux without a binding).
    Ambiguous when two agents of one kind share a cwd: the caller guards."""
    if not kind or kind not in RESUME or not RESUME[kind].fallback:
        return None
    base = list(RESUME[kind].fallback)
    return [base[0], *carried_flags(kind, original_argv), *base[1:]]


def is_verified(kind: str | None) -> bool:
    return bool(kind and kind in RESUME and RESUME[kind].verified)
