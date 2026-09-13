"""cmux backend — docs-verified only (docs/cmux-facts.md). Every verb is
UNVERIFIED-LIVE until probed on a Mac. The backend fails closed on anything
the docs do not describe: no process info, no screen read, no server stop,
no config reload, no plugin install, no headless relaunch.
"""
from __future__ import annotations

import json
from typing import Sequence

from .base import MANUAL, Capabilities, MuxPane, MuxSnapshot, MuxTab, MuxWorkspace, Status

CAPS = Capabilities(
    name="cmux", agent_detection="binding", agent_status="none", native_resume=True, process_info=False,
    screen_read=False, layout_reapply=False, live_reload=False, live_handoff="never",
    headless_start="manual", plugin_install=False, needs_viewport=False, has_server=False, docs_only=True,
)
KEYS = {"enter", "tab", "escape", "backspace", "delete", "up", "down", "left", "right"}
READONLY = {"list-workspaces", "current-workspace", "list-panels", "list-pane-surfaces", "list-notifications",
            "list-status", "list-log", "sidebar-state", "ping", "capabilities", "identify"}


class CmuxBackend:
    name = "cmux"
    caps = CAPS

    def __init__(self, socket: str | None = None):
        self.socket = socket

    def cli_prefix(self, session: str) -> list[str]:
        return ["cmux", "--socket", self.socket] if self.socket else ["cmux"]

    # -- read-only ---------------------------------------------------
    def status_argv(self) -> list[str]:
        return ["capabilities", "--json"]

    def parse_status(self, stdout: str, ok: bool) -> Status:
        if not ok:
            return Status(running=False)
        try:
            d = json.loads(stdout)
        except ValueError:
            d = {}
        return Status(running=True, version=str(d.get("version") or "") or None, raw=d)

    def snapshot_argvs(self) -> list[list[str]]:
        return [["list-workspaces", "--json", "--id-format", "uuids"], ["list-panels", "--json", "--id-format", "uuids"],
                ["list-pane-surfaces", "--json", "--id-format", "uuids"]]

    def parse_snapshot(self, outputs: Sequence[str]) -> MuxSnapshot:
        """Field names are UNVERIFIED-LIVE; the parser accepts the obvious
        candidates (``id``/``uuid``, ``title``/``name``, ``workspace``/
        ``workspace_id``, ``panel``/``panel_id``, ``cwd``) and leaves the
        rest empty rather than guessing."""
        def load(s):
            try:
                d = json.loads(s or "null")
            except ValueError:
                return []
            if isinstance(d, dict):
                for k in ("workspaces", "panels", "surfaces", "items", "result"):
                    if isinstance(d.get(k), list):
                        return d[k]
            return d if isinstance(d, list) else []
        ws_raw, panel_raw, surf_raw = (list(outputs) + ["", "", ""])[:3]
        ms = MuxSnapshot(mux="cmux")
        gid = lambda o, *ks: next((str(o[k]) for k in ks if o.get(k) is not None), None)  # noqa: E731
        for n, w in enumerate(load(ws_raw), 1):
            wid = gid(w, "uuid", "id", "workspace_id") or f"ws{n}"
            ms.workspaces.append(MuxWorkspace(wid, gid(w, "title", "name", "label") or f"workspace{n}", n))
        for n, p in enumerate(load(panel_raw), 1):
            pid = gid(p, "uuid", "id", "panel_id") or f"panel{n}"
            ms.tabs.append(MuxTab(pid, gid(p, "workspace", "workspace_id", "workspace_uuid") or "", f"panel{n}", n))
        for n, s in enumerate(load(surf_raw), 1):
            sid = gid(s, "uuid", "id", "surface_id") or f"s{n}"
            ms.panes.append(MuxPane(pane_id=sid, workspace_id=gid(s, "workspace", "workspace_id", "workspace_uuid") or "",
                                    tab_id=gid(s, "panel", "panel_id", "panel_uuid") or "", index=n,
                                    cwd=gid(s, "cwd", "working_directory") or "", title=gid(s, "title", "name") or "",
                                    extra={"raw": s}))
        return ms

    def process_info_argv(self, pane: MuxPane) -> list[str] | None:
        return None   # absent from the docs

    def parse_process_info(self, pane: MuxPane, stdout: str) -> dict:
        return {"pane_id": pane.pane_id, "foreground_processes": []}

    def excerpt_argv(self, pane_id: str, lines: int) -> list[str] | None:
        return None   # absent from the docs

    def resume_binding_argv(self, surface_id: str) -> list[str]:
        return ["surface", "resume", "show", "--json", "--surface", surface_id]

    # -- mutation ----------------------------------------------------
    def send_text(self, pane_id: str, text: str) -> list[str]:
        return ["send", "--surface", pane_id, text]

    def clear_input(self, pane_id: str) -> list[str] | None:
        return None   # the documented send-key set has no control keys

    def send_enter(self, pane_id: str) -> list[str]:
        return ["send-key", "--surface", pane_id, "enter"]

    def interrupt(self, pane_id: str) -> list[str]:
        return ["send-key", "--surface", pane_id, "escape"]   # no ctrl-c key documented; escape is

    def workspace_create(self, label: str, cwd: str, env: dict | None = None) -> list[str]:
        return ["new-workspace"]   # no --label/--cwd documented; label applied later is UNVERIFIED

    def tab_create(self, workspace_ref: str, label: str, cwd: str, env: dict | None = None) -> list[str] | None:
        return None   # panels are created by splitting

    def pane_split(self, pane_ref: str, direction: str, cwd: str, env: dict | None = None) -> list[str]:
        return ["new-split", direction if direction in ("right", "down", "left", "up") else "right"]

    def layout_apply(self, tab_ref: str, layout: str) -> list[str] | None:
        return None

    def pane_run(self, pane_ref: str, argv: Sequence[str], cwd: str) -> list[str]:
        import shlex
        return ["send", "--surface", pane_ref, shlex.join(argv)]   # then send_enter

    def agent_start(self, name: str, kind: str, pane_ref: str, resume_argv: Sequence[str] | None,
                    flags: Sequence[str] | None = None) -> list[list[str]]:
        import shlex
        from ..classify import AGENT_KINDS
        argv = list(resume_argv) if resume_argv else [AGENT_KINDS.get(kind, kind), *(flags or [])]
        return [self.send_text(pane_ref, shlex.join(argv)), self.send_enter(pane_ref)]

    def agent_wait_exit(self, pane_ref: str, kind: str | None, timeout_ms: int) -> list[str]:
        return [MANUAL, f"confirm that the agent in surface {pane_ref} has exited"]

    def agent_wait_idle(self, target: str, timeout_ms: int) -> list[str]:
        return [MANUAL, f"confirm that the agent in surface {target} is idle at its prompt"]

    def agent_prompt(self, target: str, text: str) -> list[list[str]]:
        return [self.send_text(target, text), self.send_enter(target)]

    def agent_get(self, target: str) -> list[str] | None:
        return None

    def workspace_close(self, ws_ref: str) -> list[str]:
        return ["close-workspace", "--workspace", ws_ref]

    def session_stop(self, session: str) -> list[str] | None:
        return None   # no server; quitting the app is manual

    def server_stop(self) -> list[str] | None:
        return None

    def session_start(self, session: str, first_label: str, cwd: str, window: str | None = None,
                      env: dict | None = None) -> list[str] | None:
        return None   # GUI relaunch is manual; then `restore-session`

    def restore_session(self) -> list[str]:
        return ["restore-session"]

    def reload_config(self) -> list[str] | None:
        return None

    def is_mutating(self, argv: Sequence[str]) -> bool:
        a = list(argv)
        if a[:1] == [MANUAL]:
            return False
        if a[:3] == ["surface", "resume", "show"]:
            return False
        return not (a and a[0] in READONLY)

    def created_ids(self, stdout: str) -> dict:
        try:
            d = json.loads(stdout)
        except ValueError:
            return {}
        out = {}
        for k in ("surface", "surface_id", "surface_uuid"):
            if d.get(k):
                out["pane_id"] = str(d[k])
        for k in ("workspace", "workspace_id", "workspace_uuid", "id"):
            if d.get(k) and "pane_id" not in out:
                out["workspace_id"] = str(d[k])
        return out

    def agent_target(self, pane_id: str, name: str) -> str:
        return pane_id

    def wait_via(self) -> str:
        return "manual"
