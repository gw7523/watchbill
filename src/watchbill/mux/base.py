"""Mux backend protocol: the multiplexer that owns the PTYs on a host.

A backend is *pure*: it builds argv and parses output. It never runs
anything; transports run, ``exec`` decides whether to. Three
implementations: :mod:`herdr` (native agent awareness), :mod:`tmux`
(derived agent awareness), :mod:`cmux` (docs-verified only).

Backend-neutral shapes (:class:`MuxSnapshot`) feed ``collect.build_roster``
so the roster, planners and identity rules are the same for every mux.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Sequence

MUXES = ("herdr", "tmux", "cmux")


@dataclass(frozen=True)
class Capabilities:
    name: str
    agent_detection: str        # native | derived | binding | none
    agent_status: str           # native | heuristic | none
    native_resume: bool         # a per-agent resume id / binding exists
    process_info: bool          # cwd + foreground argv per pane
    screen_read: bool           # excerpts
    layout_reapply: bool        # exact layout string can be re-applied
    live_reload: bool           # config reload without dropping PTYs
    live_handoff: str           # never | official-only
    headless_start: str         # verified | unverified | manual
    plugin_install: bool
    needs_viewport: bool        # #2064: a client must be attached before agents resume
    has_server: bool            # session/server stop is a thing
    docs_only: bool = False     # every verb is UNVERIFIED-LIVE


@dataclass
class MuxWorkspace:
    workspace_id: str
    label: str
    number: int | None = None


@dataclass
class MuxTab:
    tab_id: str
    workspace_id: str
    label: str
    number: int | None = None
    layout: str | None = None            # tmux window_layout; re-applicable
    splits: list = field(default_factory=list)   # herdr layout splits, verbatim


@dataclass
class MuxPane:
    pane_id: str
    workspace_id: str
    tab_id: str
    index: int = 1
    cwd: str = ""
    foreground_cwd: str | None = None
    title: str = ""
    name: str | None = None              # operator-set label if the mux has one
    agent: str | None = None             # kind, when the mux knows
    agent_status: str | None = None
    agent_session: dict | None = None
    rect: dict | None = None
    pid: int | None = None               # shell pid (tmux pane_pid)
    tty: str | None = None
    current_command: str | None = None   # tmux pane_current_command
    activity_age_s: float | None = None  # seconds since last output (tmux)
    terminal_id: str | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class MuxSnapshot:
    mux: str
    version: str | None = None
    workspaces: list[MuxWorkspace] = field(default_factory=list)
    tabs: list[MuxTab] = field(default_factory=list)
    panes: list[MuxPane] = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    def tab(self, tab_id: str) -> MuxTab | None:
        return next((t for t in self.tabs if t.tab_id == tab_id), None)


@dataclass(frozen=True)
class Status:
    running: bool
    version: str | None = None
    protocol: int | None = None
    socket: str | None = None
    live_handoff_flag: bool = False
    restart_needed: bool = False
    raw: dict = field(default_factory=dict)


class MuxBackend(Protocol):
    """Every method returns argv (or a list of argv for multi-step ops) and
    never executes. ``None`` means the mux cannot do it; planners turn that
    into a Refusal or a note."""

    name: str
    caps: Capabilities

    def cli_prefix(self, session: str) -> list[str]: ...
    # read-only
    def status_argv(self) -> list[str]: ...
    def parse_status(self, stdout: str, ok: bool) -> Status: ...
    def snapshot_argvs(self) -> list[list[str]]: ...
    def parse_snapshot(self, outputs: Sequence[str]) -> MuxSnapshot: ...
    def process_info_argv(self, pane: MuxPane) -> list[str] | None: ...
    def parse_process_info(self, pane: MuxPane, stdout: str) -> dict: ...
    def excerpt_argv(self, pane_id: str, lines: int) -> list[str] | None: ...
    # mutation
    def send_text(self, pane_id: str, text: str) -> list[str]: ...
    def send_enter(self, pane_id: str) -> list[str]: ...
    def clear_input(self, pane_id: str) -> list[str] | None: ...
    def interrupt(self, pane_id: str) -> list[str]: ...
    def workspace_create(self, label: str, cwd: str) -> list[str]: ...
    def tab_create(self, workspace_ref: str, label: str, cwd: str) -> list[str] | None: ...
    def pane_split(self, pane_ref: str, direction: str, cwd: str) -> list[str]: ...
    def layout_apply(self, tab_ref: str, layout: str) -> list[str] | None: ...
    def pane_run(self, pane_ref: str, argv: Sequence[str], cwd: str) -> list[str]: ...
    def agent_start(self, name: str, kind: str, pane_ref: str, resume_argv: Sequence[str] | None) -> list[list[str]]: ...
    def agent_wait_exit(self, pane_ref: str, kind: str | None, timeout_ms: int) -> list[str]: ...
    def agent_wait_idle(self, target: str, timeout_ms: int) -> list[str]: ...
    def agent_prompt(self, target: str, text: str) -> list[list[str]]: ...
    def agent_get(self, target: str) -> list[str] | None: ...
    def workspace_close(self, ws_ref: str) -> list[str]: ...
    def session_stop(self, session: str) -> list[str] | None: ...
    def server_stop(self) -> list[str] | None: ...
    def session_start(self, session: str, first_label: str, cwd: str, window: str | None = None,
                      env: dict | None = None) -> list[str] | None: ...
    def reload_config(self) -> list[str] | None: ...
    def is_mutating(self, argv: Sequence[str]) -> bool: ...
    def created_ids(self, stdout: str) -> dict: ...
    def agent_target(self, pane_id: str, name: str) -> str: ...
    def wait_via(self) -> str: ...   # "mux" (the CLI waits) or "shell" (a remote poll loop)


POLL = "__poll__"       # backend asks for a remote shell poll loop (no native wait primitive)
MANUAL = "__manual__"   # backend asks the operator to do it by hand


def poll_loop(cmd: str, *, timeout_ms: int, invert: bool = False, interval_s: int = 1) -> list[str]:
    """A portable ``sh -c`` wait: run ``cmd`` until it succeeds (or, with
    ``invert``, until it fails), up to ``timeout_ms``. Exit 0 = condition met,
    1 = timed out. Used where the mux has no primitive for the condition —
    herdr cannot wait for an agent to *disappear*, tmux cannot wait at all."""
    n = max(1, timeout_ms // (interval_s * 1000))
    test = f"{cmd} >/dev/null 2>&1"
    hit = f"{test} || exit 0" if invert else f"{test} && exit 0"
    return ["sh", "-c", f"for i in $(seq 1 {n}); do {hit}; sleep {interval_s}; done; exit 1"]


def strip_prefix(argv: Sequence[str], prefix: Sequence[str]) -> list[str]:
    a = list(argv)
    if a[:len(prefix)] == list(prefix):
        return a[len(prefix):]
    return a
