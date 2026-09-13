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

from .base import POLL, Capabilities, MuxPane, MuxSnapshot, MuxTab, MuxWorkspace, Status, poll_loop

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
                # window_activity is WINDOW-scoped: a quiet agent sharing a window
                # with a noisy pane reads as `working` (safe — parking refuses),
                # and a blocked agent in a quiet window whose dialog matches no
                # pattern reads as `idle` (unsafe — hence the screen check in
                # detect.looks_blocked and again in exec before any key is sent).
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
        tty = shlex.quote(pane.tty.removeprefix("/dev/"))
        # Portable across GNU and BSD: no `ps --sort`, and the cwd comes from
        # /proc on Linux or lsof on macOS. It must exit 0: on macOS the old
        # Linux-only tail failed, the probe exited 1, and the collector threw
        # away a process list that had worked (Mac mini, 2026-09-13).
        return ["sh", "-c",
                f"ps -t {tty} -o pid=,ppid=,stat=,args= 2>/dev/null; "
                f"p=$(ps -t {tty} -o pid=,stat= 2>/dev/null | awk '$2 ~ /\\+/ {{print $1}}' | tail -1); "
                f'[ -n "$p" ] && {{ readlink /proc/$p/cwd 2>/dev/null || lsof -a -p "$p" -d cwd -Fn 2>/dev/null | sed -n "s/^n//p"; }}; true']

    def parse_process_info(self, pane: MuxPane, stdout: str) -> dict:
        """``ps`` rows; foreground = STAT contains ``+``. Last line may be the
        cwd of the youngest process (Linux /proc).

        A pane's own process is normally the shell, so it is skipped — but
        after ``respawn-pane -k`` the relaunched command *is* ``pane_pid``.
        Dropping it would classify a running watcher as an empty shell and the
        next ``set`` would not bring it back, so it is kept when it is the only
        foreground process and is not a shell.
        """
        procs, cwd, own = [], None, None
        for line in stdout.splitlines():
            parts = line.split(None, 3)
            if len(parts) >= 4 and parts[0].isdigit():
                pid, ppid, stat, args = parts
                if "+" not in stat:
                    continue
                argv = shlex.split(args) if args else []
                rec = {"pid": int(pid), "ppid": int(ppid), "argv": argv, "cmdline": args,
                       "name": argv[0].rsplit("/", 1)[-1] if argv else "", "cwd": pane.cwd}
                if int(pid) == pane.pid:
                    own = rec
                else:
                    procs.append(rec)
            elif line.startswith("/"):
                cwd = line.strip()
        if not procs and own and own["name"] not in ("bash", "zsh", "sh", "fish", "-bash", "-zsh"):
            procs = [own]
        for p in procs:
            p["cwd"] = cwd or pane.cwd
        return {"pane_id": pane.pane_id, "shell_pid": pane.pid, "foreground_processes": procs,
                "foreground_process_group_id": procs[0]["pid"] if procs else None}

    def excerpt_argv(self, pane_id: str, lines: int) -> list[str] | None:
        return ["capture-pane", "-p", "-t", pane_id, "-S", f"-{lines}"]

    # -- mutation ----------------------------------------------------
    def send_text(self, pane_id: str, text: str) -> list[str]:
        return ["send-keys", "-t", pane_id, "-l", text]

    def clear_input(self, pane_id: str) -> list[str] | None:
        """Discard pending input (verified in the Claude and Grok TUIs: C-u
        empties the prompt line and does not exit)."""
        return ["send-keys", "-t", pane_id, "C-u"]

    def send_enter(self, pane_id: str) -> list[str]:
        return ["send-keys", "-t", pane_id, "Enter"]

    def interrupt(self, pane_id: str) -> list[str]:
        return ["send-keys", "-t", pane_id, "C-c"]

    @staticmethod
    def _env(env: dict | None) -> list[str]:
        out: list[str] = []
        for k, v in sorted((env or {}).items()):
            out += ["-e", f"{k}={v}"]
        return out

    def workspace_create(self, label: str, cwd: str, window: str | None = None, env: dict | None = None) -> list[str]:
        # `-n` names the first window. Without it the window is auto-named after
        # its process, and `select-layout -t <session>:<window-name>` (the only
        # path that re-applies a recorded layout) would target nothing.
        argv = ["new-session", "-d", "-s", label, "-c", cwd, *self._env(env)]
        if window:
            argv += ["-n", window]
        return [*argv, "-P", "-F", "#{session_id}|#{pane_id}"]

    def tab_create(self, workspace_ref: str, label: str, cwd: str, env: dict | None = None) -> list[str] | None:
        return ["new-window", "-d", "-t", workspace_ref, "-n", label, "-c", cwd, *self._env(env), "-P", "-F", "#{window_id}|#{pane_id}"]

    def pane_split(self, pane_ref: str, direction: str, cwd: str, env: dict | None = None) -> list[str]:
        flag = "-h" if direction == "right" else "-v"
        return ["split-window", "-d", "-t", pane_ref, flag, "-c", cwd, *self._env(env), "-P", "-F", "#{pane_id}"]

    def layout_apply(self, tab_ref: str, layout: str) -> list[str] | None:
        return ["select-layout", "-t", tab_ref, layout]

    def pane_run(self, pane_ref: str, argv: Sequence[str], cwd: str) -> list[str]:
        return ["respawn-pane", "-k", "-t", pane_ref, "-c", cwd, *argv]

    def agent_start(self, name: str, kind: str, pane_ref: str, resume_argv: Sequence[str] | None,
                    flags: Sequence[str] | None = None) -> list[list[str]]:
        from ..classify import AGENT_KINDS
        argv = list(resume_argv) if resume_argv else [AGENT_KINDS.get(kind, kind), *(flags or [])]
        return [self.send_text(pane_ref, shlex.join(argv)), self.send_enter(pane_ref)]

    def _poll(self, session: str, pane_ref: str, cond: str, timeout_ms: int, nudge: bool = False) -> list[str]:
        """One shell loop that exposes both signals the condition may use:
        ``$c`` the pane's foreground command, ``$q`` seconds since the window
        last produced output. tmux has no wait primitive, so every wait is
        this loop."""
        n = max(1, timeout_ms // 1000)
        fmt = "#{pane_current_command}|#{window_activity}"
        read = (f'o=$(tmux -L {shlex.quote(session)} display-message -t {shlex.quote(pane_ref)} -p {shlex.quote(fmt)}); '
                'c=${o%%|*}; a=${o##*|}; q=$(( $(date +%s) - ${a:-0} ))')
        poke = (f'; [ "$i" = 4 ] && tmux -L {shlex.quote(session)} send-keys -t {shlex.quote(pane_ref)} Enter'
                if nudge else "")
        cmd = f'for i in $(seq 1 {n}); do {read}; {cond} && exit 0{poke}; sleep 1; done; exit 1'
        return ["sh", "-c", cmd]

    SHELLS = ("bash", "zsh", "sh", "fish", "dash", "ksh")

    def agent_wait_exit(self, pane_ref: str, kind: str | None, timeout_ms: int) -> list[str]:
        """Exited = the pane's shell is back in the foreground. Comparing against
        the agent's own name fails for agents behind an interpreter: Grok shows
        as `node` from the start, so "not grok" would be true immediately."""
        cond = " || ".join(f'[ "$c" = {s} ]' for s in self.SHELLS)
        return [POLL, pane_ref, cond, str(timeout_ms), "nudge"]

    def agent_wait_idle(self, target: str, timeout_ms: int) -> list[str]:
        """Idle on tmux = the agent binary is in the foreground AND the window
        has been quiet for ``idle_after_s``. Waiting only for the binary would
        let the next step type into an agent that is still mid-turn (or sitting
        on an approval dialog); the prompt step's precondition then re-reads the
        screen for a dialog before any key is sent."""
        not_shell = " && ".join(f'[ "$c" != {sh} ]' for sh in self.SHELLS)
        cond = f'[ -n "$c" ] && {not_shell} && [ "$q" -ge {int(self.idle_after_s)} ]'
        return [POLL, target, cond, str(timeout_ms)]

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

    def session_start(self, session: str, first_label: str, cwd: str, window: str | None = None,
                      env: dict | None = None) -> list[str] | None:
        # `new-session -d` starts the server AND makes the first workspace.
        return self.workspace_create(first_label, cwd, window, env)

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
        """``POLL`` placeholder → the remote shell loop (needs the session)."""
        a = list(argv)
        if a[:1] != [POLL]:
            return a
        _, pane_ref, cond, timeout, *extra = a
        return self._poll(session, pane_ref, cond, int(timeout), nudge="nudge" in extra)
