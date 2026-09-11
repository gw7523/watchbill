"""tmux 3.x backend. Verified on 3.7c: docs/tmux-3.7-facts.md.

No agent awareness in the mux: role/kind come from ``ps -t <pane_tty>``
(pitfall 3 still holds — argv, not window names), status from
:mod:`watchbill.detect` (quiet time + approval-prompt patterns), resume from
the cwd-scoped ``--continue`` forms with a cwd-uniqueness guard.

Waits are remote shell poll loops (``wait_via() == "shell"``) because tmux
has no "wait for the agent to become idle" primitive.
"""
from __future__ import annotations

import shlex
import time
from typing import Sequence

from .base import Capabilities, MuxPane, MuxSnapshot, MuxTab, MuxWorkspace, Status

CAPS = Capabilities(
    name="tmux", agent_detection="derived", agent_status="heuristic", native_resume=False, process_info=True,
    screen_read=True, layout_reapply=True, live_reload=True, live_handoff="never",
    headless_start="verified", plugin_install=True, needs_viewport=False, has_server=True,
)

SEP = "|"
PANE_FMT = SEP.join(("#{session_name}", "#{session_id}", "#{window_index}", "#{window_id}", "#{window_name}", "#{pane_index}",
                     "#{pane_id}", "#{pane_pid}", "#{pane_current_command}", "#{pane_current_path}", "#{pane_title}",
                     "#{pane_active}", "#{window_activity}", "#{pane_width}x#{pane_height}", "#{pane_left},#{pane_top}", "#{pane_tty}"))
WIN_FMT = SEP.join(("#{session_name}", "#{window_index}", "#{window_id}", "#{window_name}", "#{window_layout}", "#{window_panes}"))
SES_FMT = SEP.join(("#{session_name}", "#{session_id}", "#{session_windows}", "#{session_attached}", "#{session_created}"))
STATUS_FMT = SEP.join(("#{version}", "#{socket_path}", "#{pid}"))

READONLY = {"list-sessions", "ls", "list-windows", "lsw", "list-panes", "lsp", "display-message", "display",
            "capture-pane", "capturep", "show-options", "show", "has-session", "has", "list-commands", "lscm", "show-environment"}


class TmuxBackend:
    name = "tmux"
    caps = CAPS

    def __init__(self, idle_after_s: float = 30.0, conf: str = "~/.tmux.conf"):
        self.idle_after_s = idle_after_s
        self.conf = conf

    def cli_prefix(self, session: str) -> list[str]:
        return ["tmux", "-L", session]

    # -- read-only ---------------------------------------------------
    def status_argv(self) -> list[str]:
        return ["display-message", "-p", STATUS_FMT]

    def parse_status(self, stdout: str, ok: bool) -> Status:
        if not ok or not stdout.strip():
            return Status(running=False)
        version, socket, pid = (stdout.strip().split(SEP) + ["", "", ""])[:3]
        return Status(running=True, version=version, socket=socket, raw={"pid": pid})

    def snapshot_argvs(self) -> list[list[str]]:
        return [["list-sessions", "-F", SES_FMT], ["list-windows", "-a", "-F", WIN_FMT], ["list-panes", "-a", "-F", PANE_FMT]]

    def parse_snapshot(self, outputs: Sequence[str], now: float | None = None) -> MuxSnapshot:
        now = time.time() if now is None else now
        ms = MuxSnapshot(mux="tmux")
        ses_out, win_out, pane_out = (list(outputs) + ["", "", ""])[:3]
        for n, line in enumerate(ses_out.splitlines(), 1):
            f = line.split(SEP)
            if len(f) < 2:
                continue
            ms.workspaces.append(MuxWorkspace(workspace_id=f[1], label=f[0], number=n))
        ses_by_name = {w.label: w.workspace_id for w in ms.workspaces}
        for line in win_out.splitlines():
            f = line.split(SEP)
            if len(f) < 6:
                continue
            sname, widx, wid, wname, layout, _n = f[:6]
            ms.tabs.append(MuxTab(tab_id=wid, workspace_id=ses_by_name.get(sname, sname), label=wname or widx,
                                  number=int(widx) if widx.isdigit() else None, layout=layout))
        for line in pane_out.splitlines():
            f = line.split(SEP)
            if len(f) < 16:
                continue
            (sname, sid, widx, wid, wname, pidx, pid_, ppid, cmd, path, title, active, act, size, pos, tty) = f[:16]
            w, h = (size.split("x") + ["0", "0"])[:2]
            x, y = (pos.split(",") + ["0", "0"])[:2]
            try:
                age = max(0.0, now - float(act))
            except ValueError:
                age = None
            ms.panes.append(MuxPane(
                pane_id=pid_, workspace_id=sid, tab_id=wid, index=int(pidx) if pidx.isdigit() else 1,
                cwd=path, foreground_cwd=None, title=title, pid=int(ppid) if ppid.isdigit() else None, tty=tty,
                current_command=cmd, activity_age_s=age,
                rect={"x": int(x), "y": int(y), "width": int(w), "height": int(h)},
            ))
        return ms

    def process_info_argv(self, pane: MuxPane) -> list[str] | None:
        if not pane.tty:
            return None
        tty = pane.tty.removeprefix("/dev/")
        return ["sh", "-c", f"ps -t {shlex.quote(tty)} -o pid=,ppid=,stat=,args= 2>/dev/null; "
                            f"readlink /proc/$(ps -t {shlex.quote(tty)} -o pid= --sort=-pid | head -1)/cwd 2>/dev/null"]

    def parse_process_info(self, pane: MuxPane, stdout: str) -> dict:
        """``ps`` rows; foreground = STAT contains ``+``. Last line may be the
        cwd of the youngest process (Linux /proc)."""
        procs, cwd = [], None
        for line in stdout.splitlines():
            parts = line.split(None, 3)
            if len(parts) >= 4 and parts[0].isdigit():
                pid, ppid, stat, args = parts
                if "+" in stat and int(pid) != pane.pid:
                    argv = shlex.split(args) if args else []
                    procs.append({"pid": int(pid), "ppid": int(ppid), "argv": argv, "cmdline": args,
                                  "name": argv[0].rsplit("/", 1)[-1] if argv else "", "cwd": pane.cwd})
            elif line.startswith("/"):
                cwd = line.strip()
        for p in procs:
            p["cwd"] = cwd or pane.cwd
        return {"pane_id": pane.pane_id, "shell_pid": pane.pid, "foreground_processes": procs,
                "foreground_process_group_id": procs[0]["pid"] if procs else None}

    def excerpt_argv(self, pane_id: str, lines: int) -> list[str] | None:
        return ["capture-pane", "-p", "-t", pane_id, "-S", f"-{lines}"]

    # -- mutation ----------------------------------------------------
    def send_text(self, pane_id: str, text: str) -> list[str]:
        return ["send-keys", "-t", pane_id, "-l", text]

    def send_enter(self, pane_id: str) -> list[str]:
        return ["send-keys", "-t", pane_id, "Enter"]

    def interrupt(self, pane_id: str) -> list[str]:
        return ["send-keys", "-t", pane_id, "C-c"]

    def workspace_create(self, label: str, cwd: str) -> list[str]:
        return ["new-session", "-d", "-s", label, "-c", cwd, "-P", "-F", "#{session_id}|#{pane_id}"]

    def tab_create(self, workspace_ref: str, label: str, cwd: str) -> list[str] | None:
        return ["new-window", "-d", "-t", workspace_ref, "-n", label, "-c", cwd, "-P", "-F", "#{window_id}|#{pane_id}"]

    def pane_split(self, pane_ref: str, direction: str, cwd: str) -> list[str]:
        flag = "-h" if direction == "right" else "-v"
        return ["split-window", "-d", "-t", pane_ref, flag, "-c", cwd, "-P", "-F", "#{pane_id}"]

    def layout_apply(self, tab_ref: str, layout: str) -> list[str] | None:
        return ["select-layout", "-t", tab_ref, layout]

    def pane_run(self, pane_ref: str, argv: Sequence[str], cwd: str) -> list[str]:
        return ["respawn-pane", "-k", "-t", pane_ref, "-c", cwd, *argv]

    def agent_start(self, name: str, kind: str, pane_ref: str, resume_argv: Sequence[str] | None) -> list[list[str]]:
        from ..classify import AGENT_KINDS
        argv = list(resume_argv) if resume_argv else [AGENT_KINDS.get(kind, kind)]
        return [self.send_text(pane_ref, shlex.join(argv)), self.send_enter(pane_ref)]

    def _poll(self, session: str, pane_ref: str, cond: str, timeout_ms: int) -> list[str]:
        n = max(1, timeout_ms // 1000)
        cmd = (f'for i in $(seq 1 {n}); do c=$(tmux -L {shlex.quote(session)} display-message -t {shlex.quote(pane_ref)} '
               f'-p "#{{pane_current_command}}"); {cond} && exit 0; sleep 1; done; exit 1')
        return ["sh", "-c", cmd]

    def agent_wait_exit(self, pane_ref: str, kind: str | None, timeout_ms: int) -> list[str]:
        from ..classify import AGENT_KINDS
        exe = AGENT_KINDS.get(kind or "", kind or "")
        return ["__poll__", pane_ref, f'[ "$c" != {shlex.quote(exe)} ]', str(timeout_ms)]

    def agent_wait_idle(self, target: str, timeout_ms: int) -> list[str]:
        # idle = the agent binary is in the foreground and output has been quiet; the exec-side
        # poll re-reads window_activity; here we only wait for the binary to be up.
        return ["__poll__", target, '[ -n "$c" ] && [ "$c" != bash ] && [ "$c" != zsh ] && [ "$c" != sh ] && [ "$c" != fish ]', str(timeout_ms)]

    def agent_prompt(self, target: str, text: str) -> list[list[str]]:
        return [self.send_text(target, text), self.send_enter(target)]

    def agent_get(self, target: str) -> list[str] | None:
        return ["capture-pane", "-p", "-t", target, "-S", "-15"]   # heuristic blocked check reads the screen

    def workspace_close(self, ws_ref: str) -> list[str]:
        return ["kill-session", "-t", ws_ref]

    def session_stop(self, session: str) -> list[str] | None:
        return ["kill-server"]   # tmux: the "session" slot is the server; guarded like server stop

    def server_stop(self) -> list[str] | None:
        return ["kill-server"]

    def session_start(self, session: str, first_label: str, cwd: str) -> list[str] | None:
        return self.workspace_create(first_label, cwd)   # new-session -d starts the server

    def reload_config(self) -> list[str] | None:
        return ["source-file", self.conf]

    def is_mutating(self, argv: Sequence[str]) -> bool:
        a = list(argv)
        if a[:1] == ["__poll__"] or a[:2] == ["sh", "-c"]:
            return False
        return not (a and a[0] in READONLY)

    def created_ids(self, stdout: str) -> dict:
        s = stdout.strip().splitlines()
        if not s:
            return {}
        parts = s[-1].split(SEP)
        out: dict = {}
        for p in parts:
            if p.startswith("%"):
                out["pane_id"] = p
            elif p.startswith("$"):
                out["workspace_id"] = p
            elif p.startswith("@"):
                out["tab_id"] = p
        return out

    def agent_target(self, pane_id: str, name: str) -> str:
        return pane_id

    def wait_via(self) -> str:
        return "shell"

    def resolve_poll(self, session: str, argv: Sequence[str]) -> list[str]:
        """``__poll__`` placeholder → the remote shell loop (needs the session)."""
        a = list(argv)
        if a[:1] != ["__poll__"]:
            return a
        _, pane_ref, cond, timeout = a
        return self._poll(session, pane_ref, cond, int(timeout))
