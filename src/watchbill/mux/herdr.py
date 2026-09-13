"""Herdr 0.8.2 backend. Verbs are the ones docs/herdr-0.8.2-facts.md verifies.

Native agent awareness: ``pane.agent``, ``agent_status``, ``agent_session``.
Mutating/read-only classification lives in :mod:`watchbill.plan` (the verb
tables) so ``is_mutating`` defers there.
"""
from __future__ import annotations

import json
import shlex
from typing import Sequence

from .base import POLL, Capabilities, MuxPane, MuxSnapshot, MuxTab, MuxWorkspace, Status, poll_loop

CAPS = Capabilities(
    name="herdr", agent_detection="native", agent_status="native", native_resume=True, process_info=True,
    screen_read=True, layout_reapply=False, live_reload=True, live_handoff="official-only",
    headless_start="verified", plugin_install=True, needs_viewport=True, has_server=True,
)

# States that mean "the agent has settled and will accept input". `blocked` is
# included on purpose: `agent wait` is LEVEL-triggered (verified 2026-09-11 —
# `--until done` on an already-done agent returns at once), so waiting only for
# `idle` hangs the full timeout on an agent that settled to `done` or `blocked`.
# The prompt step's precondition then refuses a blocked agent in one round trip.
SETTLED = ("idle", "done", "blocked")


def _result(stdout: str) -> dict:
    d = json.loads(stdout)
    return d.get("result", d) if isinstance(d, dict) else {}


class HerdrBackend:
    name = "herdr"
    caps = CAPS

    def cli_prefix(self, session: str) -> list[str]:
        return ["herdr", "--session", session]

    # -- read-only ---------------------------------------------------
    def status_argv(self) -> list[str]:
        return ["status", "server", "--json"]

    def parse_status(self, stdout: str, ok: bool) -> Status:
        if not ok:
            return Status(running=False)
        d = json.loads(stdout)
        return Status(running=bool(d.get("running")), version=d.get("version"), protocol=d.get("protocol"),
                      socket=d.get("socket"), live_handoff_flag=bool((d.get("capabilities") or {}).get("live_handoff")),
                      restart_needed=bool(d.get("restart_needed")), raw=d)

    def snapshot_argvs(self) -> list[list[str]]:
        return [["api", "snapshot"]]

    def parse_snapshot(self, outputs: Sequence[str]) -> MuxSnapshot:
        snap = _result(outputs[0]).get("snapshot", {})
        return snapshot_from_herdr(snap)

    def process_info_argv(self, pane: MuxPane) -> list[str] | None:
        return ["pane", "process-info", "--pane", pane.pane_id]   # explicit id, never --current

    def parse_process_info(self, pane: MuxPane, stdout: str) -> dict:
        return _result(stdout).get("process_info", {})

    def excerpt_argv(self, pane_id: str, lines: int) -> list[str] | None:
        # `--source recent` returns only output since the last read and is empty
        # on a settled pane (verified); `visible` is the viewport we want.
        return ["pane", "read", pane_id, "--source", "visible", "--lines", str(lines), "--format", "text"]

    # -- mutation ----------------------------------------------------
    def send_text(self, pane_id: str, text: str) -> list[str]:
        return ["pane", "send-text", pane_id, text]

    def send_enter(self, pane_id: str) -> list[str]:
        return ["pane", "send-keys", pane_id, "enter"]          # verified (`Enter` also accepted)

    def interrupt(self, pane_id: str) -> list[str]:
        # verified: `ctrl-c` is rejected with invalid_key; the tmux-style `C-c` is the name.
        return ["pane", "send-keys", pane_id, "C-c"]

    def workspace_create(self, label: str, cwd: str) -> list[str]:
        return ["workspace", "create", "--label", label, "--cwd", cwd, "--no-focus"]

    def tab_create(self, workspace_ref: str, label: str, cwd: str) -> list[str] | None:
        return ["tab", "create", "--workspace", workspace_ref, "--label", label, "--cwd", cwd, "--no-focus"]

    def pane_split(self, pane_ref: str, direction: str, cwd: str) -> list[str]:
        return ["pane", "split", pane_ref, "--direction", direction, "--cwd", cwd, "--no-focus"]

    def layout_apply(self, tab_ref: str, layout: str) -> list[str] | None:
        return None   # socket-only in 0.8.2 (ssh_socket transport, later)

    def pane_run(self, pane_ref: str, argv: Sequence[str], cwd: str) -> list[str]:
        return ["pane", "run", pane_ref, *argv]

    def agent_start(self, name: str, kind: str, pane_ref: str, resume_argv: Sequence[str] | None) -> list[list[str]]:
        argv = ["agent", "start", name, "--kind", kind, "--pane", pane_ref]
        if resume_argv:
            argv += ["--", *resume_argv]
        return [argv]

    def agent_wait_exit(self, pane_ref: str, kind: str | None, timeout_ms: int) -> list[str]:
        """Herdr has no "wait until the agent is gone" primitive: once the agent
        exits the pane simply has none, and `agent wait` answers
        `agent_not_found` (verified). `--until unknown` is a *state* an agent
        that is still running can be in, so it is the wrong oracle. Poll
        `agent get` until it fails instead."""
        return [POLL, pane_ref, "gone", str(timeout_ms)]

    def agent_wait_idle(self, target: str, timeout_ms: int) -> list[str]:
        argv = ["agent", "wait", target]
        for st in SETTLED:
            argv += ["--until", st]
        return [*argv, "--timeout", str(timeout_ms)]

    def agent_prompt(self, target: str, text: str) -> list[list[str]]:
        return [["agent", "prompt", target, text, "--wait", "--until", "working", "--timeout", "10000"]]

    def agent_get(self, target: str) -> list[str] | None:
        return ["agent", "get", target]

    def workspace_close(self, ws_ref: str) -> list[str]:
        return ["workspace", "close", ws_ref]

    def session_stop(self, session: str) -> list[str] | None:
        return ["session", "stop", session]

    def server_stop(self) -> list[str] | None:
        return ["server", "stop"]

    def session_start(self, session: str, first_label: str, cwd: str, window: str | None = None) -> list[str] | None:
        # Verified 2026-09-11: `herdr --session <name> server` starts a detached
        # headless server. hosts.toml `start` still wins when a host sets one
        # (Omarchy boxes prefer their systemd user unit).
        return ["server"]

    def reload_config(self) -> list[str] | None:
        return ["server", "reload-config"]

    def is_mutating(self, argv: Sequence[str]) -> bool:
        from ..plan import is_mutating
        return is_mutating(["herdr", *argv])

    def created_ids(self, stdout: str) -> dict:
        try:
            r = _result(stdout)
        except ValueError:
            return {}
        out = {}
        pid = (r.get("root_pane") or {}).get("pane_id") or (r.get("pane") or {}).get("pane_id")
        if pid:
            out["pane_id"] = pid
        wsid = (r.get("workspace") or {}).get("workspace_id")
        if wsid:
            out["workspace_id"] = wsid
        return out

    def agent_target(self, pane_id: str, name: str) -> str:
        return name

    def wait_via(self) -> str:
        return "mux"

    def server_up_poll(self, session: str, timeout_ms: int) -> list[str]:
        """Wait until the session's server is really up. `status server --json`
        exits 0 for a STOPPED session too (it prints `"running":false`), so
        the exit code proves nothing; match the field."""
        status = shlex.join(["herdr", "--session", session, "status", "server", "--json"])
        return poll_loop(f"{status} | grep -q '\"running\":true'", timeout_ms=timeout_ms)

    def resolve_poll(self, session: str, argv: Sequence[str]) -> list[str]:
        """``[POLL, pane, "gone", timeout]`` → a shell loop that ends when the
        pane no longer hosts an agent."""
        a = list(argv)
        if a[:1] != [POLL]:
            return a
        _, pane_ref, cond, timeout = a
        get = shlex.join(["herdr", "--session", session, "agent", "get", pane_ref])
        return poll_loop(get, timeout_ms=int(timeout), invert=(cond == "gone"))


def snapshot_from_herdr(snap: dict) -> MuxSnapshot:
    """``api snapshot`` → MuxSnapshot. Rects come from ``layouts``."""
    rects = {p["pane_id"]: p.get("rect") for lay in snap.get("layouts", []) for p in lay.get("panes", [])}
    order: dict[str, list[str]] = {}
    for lay in snap.get("layouts", []):
        order[lay["tab_id"]] = [p["pane_id"] for p in lay.get("panes", [])]
    ms = MuxSnapshot(mux="herdr", version=snap.get("version"), extra={"protocol": snap.get("protocol")})
    for ws in snap.get("workspaces", []):
        ms.workspaces.append(MuxWorkspace(ws["workspace_id"], ws.get("label") or f"ws{ws.get('number')}", ws.get("number")))
    tabs = {t["tab_id"]: t for t in snap.get("tabs", [])}
    for lay in snap.get("layouts", []):
        t = tabs.get(lay["tab_id"], {})
        ms.tabs.append(MuxTab(lay["tab_id"], lay["workspace_id"], str(t.get("label") or t.get("number") or "1"),
                              t.get("number"), None, lay.get("splits", [])))
    for t_id, t in tabs.items():
        if ms.tab(t_id) is None:
            ms.tabs.append(MuxTab(t_id, t["workspace_id"], str(t.get("label") or t.get("number") or "1"), t.get("number")))
    for p in snap.get("panes", []):
        pid = p["pane_id"]
        lst = order.setdefault(p["tab_id"], [])
        if pid not in lst:
            lst.append(pid)
        ms.panes.append(MuxPane(
            pane_id=pid, workspace_id=p["workspace_id"], tab_id=p["tab_id"], index=lst.index(pid) + 1,
            cwd=p.get("cwd") or "", foreground_cwd=p.get("foreground_cwd") or None,
            title=p.get("terminal_title_stripped") or "", name=p.get("name") or p.get("agent_name"),
            agent=p.get("agent"), agent_status=p.get("agent_status"), agent_session=p.get("agent_session"),
            rect=rects.get(pid), terminal_id=p.get("terminal_id"),
        ))
    return ms


def render(argv: Sequence[str]) -> str:
    return shlex.join(argv)
