"""Role classification from ``pane process-info`` argv plus ``agent list``.

Roles, first match wins::

    bridge | agent | watcher | poller | server | editor | shell

``bridge`` is any nested Herdr in the pane's foreground (``herdr --remote``,
``herdr --session``, ``herdr session attach``, ``herdr server``). Bridges are
catalogued but never relaunched: a bridge pane on host A is a viewport into a
server that Watchbill manages on host B directly.

The argv source is ``pane.process_info.foreground_processes[*].argv``. The
snapshot's ``agent`` / ``agent_status`` fields are Herdr's own detection and
outrank argv heuristics for the ``agent`` role. ``layout.export`` ``command``
fields are never consulted (pitfall 3).
"""
from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

ROLES = ("bridge", "agent", "watcher", "poller", "server", "editor", "shell")

# From `herdr agent start --help` on 0.8.2. Maps kind -> executable basename.
AGENT_KINDS: dict[str, str] = {
    "pi": "pi", "claude": "claude", "codex": "codex", "gemini": "gemini", "cursor": "cursor-agent",
    "devin": "devin", "agy": "agy", "cline": "cline", "omp": "omp", "mastracode": "mastracode",
    "opencode": "opencode", "copilot": "copilot", "kimi": "kimi", "kiro": "kiro", "droid": "droid",
    "amp": "amp", "grok": "grok", "hermes": "hermes", "kilo": "kilo", "qodercli": "qodercli",
    "qwen": "qwen", "maki": "maki",
}
_EXE_TO_KIND = {exe: kind for kind, exe in AGENT_KINDS.items()}

WATCHER_BINS = {"watchexec", "entr", "nodemon", "fswatch", "inotifywait", "cargo-watch",
                "chokidar", "reflex", "air", "modd", "tsc-watch", "jest-watch"}
POLLER_BINS = {"watch", "tail", "journalctl", "less", "htop", "btop", "top", "sleep"}
SERVER_BINS = {"uvicorn", "gunicorn", "hypercorn", "flask", "vite", "next", "webpack-dev-server",
               "http-server", "live-server", "hugo", "mkdocs", "jekyll", "php", "caddy", "nginx",
               "ollama", "quickshell"}
EDITOR_BINS = {"vim", "nvim", "vi", "hx", "helix", "nano", "emacs", "micro", "kak", "code", "zed"}

_WATCH_FLAG = re.compile(r"(^|\s)(--watch|-w|watch|dev|serve)(\s|$)")


@dataclass(frozen=True)
class Classification:
    role: str
    kind: str | None = None          # agent kind when role == agent
    argv: tuple[str, ...] = ()       # first foreground process argv (post-shell)
    cmdline: str = ""
    reason: str = ""                 # which rule fired, for `watchbill roll --explain`


def _basename(tok: str) -> str:
    return os.path.basename(tok)


def _foreground_argvs(process_info: dict | None) -> list[tuple[str, ...]]:
    if not process_info:
        return []
    procs = process_info.get("foreground_processes") or []
    return [tuple(p.get("argv") or ()) for p in procs if p.get("argv")]


def _is_python_module(argv: Sequence[str], module: str) -> bool:
    return len(argv) >= 3 and _basename(argv[0]).startswith("python") and argv[1] == "-m" and argv[2] == module


def classify(pane: dict, process_info: dict | None) -> Classification:
    """Classify one pane. ``pane`` is a snapshot/agent-list pane object."""
    argvs = _foreground_argvs(process_info)
    primary = argvs[0] if argvs else ()
    cmdline = " ".join(primary)

    # 1. bridge: any nested herdr anywhere in the foreground group.
    for av in argvs:
        if av and _basename(av[0]) == "herdr":
            return Classification("bridge", argv=av, cmdline=" ".join(av), reason="argv[0] is herdr")

    # 2. agent: Herdr detection first, then argv.
    detected = pane.get("agent")
    if detected:
        return Classification("agent", kind=detected, argv=primary, cmdline=cmdline, reason="herdr detected agent")
    for av in argvs:
        exe = _basename(av[0]) if av else ""
        if exe in _EXE_TO_KIND:
            return Classification("agent", kind=_EXE_TO_KIND[exe], argv=av, cmdline=" ".join(av),
                                  reason="argv[0] is a known agent executable")

    if not primary:
        return Classification("shell", reason="no foreground process")

    exe = _basename(primary[0])
    # 3. watcher
    if exe in WATCHER_BINS or (exe in {"cargo", "npm", "pnpm", "yarn", "bun"} and any(t in ("watch", "--watch") for t in primary[1:])):
        return Classification("watcher", argv=primary, cmdline=cmdline, reason="watcher executable or --watch")
    # 4. poller
    if exe in POLLER_BINS and (exe != "tail" or "-f" in primary or "-F" in primary):
        return Classification("poller", argv=primary, cmdline=cmdline, reason="polling executable")
    # 5. server
    if exe in SERVER_BINS or _is_python_module(primary, "http.server") or (
            exe in {"npm", "pnpm", "yarn", "bun"} and any(t in ("dev", "start", "serve") for t in primary[1:])) or (
            exe == "herdr-shell-watch"):
        return Classification("server", argv=primary, cmdline=cmdline, reason="server executable")
    # 6. editor
    if exe in EDITOR_BINS:
        return Classification("editor", argv=primary, cmdline=cmdline, reason="editor executable")
    # 7. shell (a foreground process we do not recognise is still 'shell' for
    #    restore purposes: shape only, no relaunch).
    return Classification("shell", argv=primary, cmdline=cmdline, reason="unrecognised foreground process")


# -- relaunch allowlist ----------------------------------------------------

@dataclass
class Allowlist:
    """``~/.config/watchbill/allowlist.txt``: one glob per line, ``#`` comments.

    Matched against the full cmdline of watcher/poller/server occupants. Only
    a match makes ``allow_relaunch`` true; agents use the resume path instead
    and bridges are never relaunched.
    """

    patterns: list[str] = field(default_factory=list)

    @classmethod
    def parse(cls, text: str) -> "Allowlist":
        pats = [ln.strip() for ln in text.splitlines()]
        return cls([p for p in pats if p and not p.startswith("#")])

    def allows(self, cmdline: str) -> bool:
        return any(fnmatch.fnmatchcase(cmdline, p) for p in self.patterns)


def allow_relaunch(role: str, cmdline: str, allowlist: Allowlist | None) -> bool:
    if role == "bridge":
        return False
    if role == "agent":
        return True  # via native resume argv, see resume.py
    if role in ("watcher", "poller", "server"):
        return bool(allowlist and allowlist.allows(cmdline))
    return False


def roles_matching(roles: Iterable[str]) -> frozenset[str]:
    bad = set(roles) - set(ROLES)
    if bad:
        raise ValueError(f"unknown roles: {sorted(bad)}")
    return frozenset(roles)
