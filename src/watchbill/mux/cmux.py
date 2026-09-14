"""cmux backend — verified live against cmux 0.64.22 on the Mac mini
(docs/cmux-facts.md, 2026-09-14). Everything here was run over SSH against
the real app; nothing is taken from the published reference any more.

What cmux gives that the other muxes do not:

* **Agent hooks.** The Claude wrapper (and ``cmux hooks setup`` for codex,
  grok, opencode, …) writes ``~/.cmuxterm/<agent>-hook-sessions.json``:
  session id, surface id, cwd, pid, lifecycle (``running`` / ``idle`` /
  ``needsInput``), and a sanitized launch command. ``cmux sessions list``
  reads it. That is native agent detection, native status and a native
  resume id in one listing.
* **Native restore.** On relaunch cmux rebuilds every workspace with the
  same workspace and surface UUIDs, and runs each tracked agent's own
  resume command (``claude --resume <id> --model …``) itself — even for an
  agent that was parked with ``/exit`` first. So after a restart Watchbill
  *waits* for cmux's resume and only types its own when none appears.
* **No server.** The "session" is the GUI app. Stop = quit the app
  (``osascript``; needs ``app.confirmQuit = "never"`` in
  ``~/.config/cmux/cmux.json`` or the quit dialog blocks), start =
  ``open -a cmux`` from an ssh shell (verified; the app lands in the
  logged-in desktop session, so keychain and hooks work).

Process info comes from the process table like tmux: every process cmux
spawns carries ``CMUX_SURFACE_ID`` in its environment, ``ps -E`` finds the
surface's tty, ``ps -t`` lists what runs on it. ``tree`` reports the tty
only for surfaces that have been rendered, so it is not relied on.

Refs (``workspace:2``, ``surface:1``) are positional and renumber whenever
something closes or the app relaunches; only UUIDs are stored or targeted.
"""
from __future__ import annotations

import json
import shlex
from typing import Sequence

from .base import MANUAL, POLL, Capabilities, MuxPane, MuxSnapshot, MuxTab, MuxWorkspace, Status

CAPS = Capabilities(
    name="cmux", agent_detection="native", agent_status="native", native_resume=True, process_info=True,
    screen_read=True, layout_reapply=False, live_reload=True, live_handoff="never",
    headless_start="verified", plugin_install=False, needs_viewport=False, has_server=True, docs_only=False,
)

# cmux agent lifecycle → Watchbill agent_status (herdr's vocabulary).
LIFECYCLE = {"idle": "idle", "running": "working", "launching": "working", "needsInput": "blocked",
             "ended": "done", "unknown": "unknown"}
# send-key names verified on 0.64.22: `enter`, `escape`, `ctrl-c`, `ctrl-u`
# work; tmux spellings (`C-c`, `^C`, `c-u`) are rejected as "Unknown key".
KEYS = {"enter", "escape", "tab", "backspace", "up", "down", "left", "right", "ctrl-c", "ctrl-u", "ctrl-d"}
GLOBAL_FLAGS = {"--json", "--socket", "--id-format", "--window", "--password"}
READONLY = {"tree", "list-workspaces", "list-panes", "list-pane-surfaces", "list-windows", "current-workspace",
            "read-screen", "capture-pane", "top", "ping", "version", "capabilities", "identify", "sessions",
            "list-notifications", "list-status", "list-log", "sidebar-state", "surface-health"}
READONLY_NAMESPACED = {("workspace", "list"), ("workspace", "env"), ("workspace", "status"),
                       ("surface", "resume", "show"), ("surface", "resume", "get"), ("config", "check"),
                       ("config", "path"), ("sessions", "list")}
SHELLS = ("bash", "zsh", "sh", "fish", "dash", "ksh")
QUIT_HINT = ('cmux did not quit: set app.confirmQuit = "never" in ~/.config/cmux/cmux.json '
             '(then `cmux reload-config`) so a scripted quit is not held by the confirmation dialog')


def _strip_global(argv: Sequence[str]) -> list[str]:
    a = list(argv)
    while a and a[0] in GLOBAL_FLAGS:
        a = a[1:] if a[0] == "--json" else a[2:]
    return a


class CmuxBackend:
    name = "cmux"
    caps = CAPS

    def __init__(self, socket: str | None = None, idle_after_s: float = 10.0, app: str = "cmux"):
        self.socket = socket
        self.idle_after_s = idle_after_s
        self.app = app

    def cli_prefix(self, session: str) -> list[str]:
        return ["cmux", "--socket", self.socket] if self.socket else ["cmux"]

    # -- read-only ---------------------------------------------------
    def status_argv(self) -> list[str]:
        return ["ping"]   # PONG iff the app's socket answers; `version` also prints without the app

    def parse_status(self, stdout: str, ok: bool) -> Status:
        if not ok or "PONG" not in (stdout or ""):
            return Status(running=False)
        return Status(running=True, socket=self.socket, raw={"running": True})

    def version_argv(self) -> list[str]:
        return ["version"]

    @staticmethod
    def parse_version(stdout: str) -> str | None:
        ver = (stdout or "").strip().split()
        return ver[1] if len(ver) > 1 and ver[0] == "cmux" else (ver[0] if ver else None)

    def snapshot_argvs(self) -> list[list[str]]:
        return [["--json", "--id-format", "both", "tree", "--all"],
                ["--json", "--id-format", "both", "workspace", "list"],
                ["--json", "--id-format", "uuids", "top", "--all", "--processes"],
                ["--json", "sessions", "list", "--all"],
                ["version"]]

    def parse_snapshot(self, outputs: Sequence[str]) -> MuxSnapshot:
        """tree → workspaces / panes (= Watchbill tabs) / surfaces (= Watchbill
        panes); workspace list → cwd; top → pids per surface (the shell is the
        oldest); sessions → agent kind, lifecycle, session id per surface."""
        def load(s, default):
            try:
                return json.loads(s or "null") or default
            except ValueError:
                return default
        tree_raw, ws_raw, top_raw, sess_raw, ver_raw = (list(outputs) + [""] * 5)[:5]
        ms = MuxSnapshot(mux="cmux")
        ms.version = self.parse_version(ver_raw)
        cwd_by_ws = {w.get("id"): w.get("current_directory") or "" for w in load(ws_raw, {}).get("workspaces", [])}
        pids_by_surface: dict[str, set[int]] = {}
        top = load(top_raw, {})
        for g in ((top.get("memory_diagnostic") or {}).get("children") or {}).get("groups") or []:
            for a in g.get("attributions") or []:
                sid = a.get("surface_id")
                if sid and a.get("reason") == "surface-process-tree":
                    pids_by_surface.setdefault(sid, set()).update(int(p) for p in a.get("pids") or [])
        sess_by_surface: dict[str, dict] = {}
        for s in load(sess_raw, {}).get("sessions", []):
            sid = s.get("surface_id")
            if sid and s.get("session_id") and s.get("active_for_surface", True):
                sess_by_surface[sid] = s
        n_ws = 0
        for win in load(tree_raw, {}).get("windows", []):
            for w in win.get("workspaces", []):
                n_ws += 1
                wid = str(w.get("id") or w.get("ref"))
                ms.workspaces.append(MuxWorkspace(wid, w.get("title") or f"workspace{n_ws}", n_ws))
                for pn, p in enumerate(w.get("panes", []), 1):
                    pid_ = str(p.get("id") or p.get("ref"))
                    ms.tabs.append(MuxTab(pid_, wid, f"pane{pn}", pn))
                    for sn, s in enumerate(p.get("surfaces", []), 1):
                        sid = str(s.get("id") or s.get("ref"))
                        rec = sess_by_surface.get(sid)
                        pids = sorted(pids_by_surface.get(sid, ()))
                        pane = MuxPane(pane_id=sid, workspace_id=wid, tab_id=pid_, index=sn,
                                       cwd=cwd_by_ws.get(wid, ""), title=s.get("title") or "", tty=s.get("tty") or None,
                                       pid=pids[0] if pids else None,
                                       extra={"surface_type": s.get("type"), "pids": pids, "ref": s.get("ref")})
                        if s.get("type") not in (None, "terminal"):
                            pane.extra["non_terminal"] = True
                        if rec:
                            pane.agent = rec.get("agent")
                            pane.agent_status = LIFECYCLE.get(rec.get("agent_lifecycle") or "unknown", "unknown")
                            pane.agent_session = {"value": rec["session_id"], "source": "cmux hook store",
                                                  "lifecycle": rec.get("agent_lifecycle"),
                                                  "restorable": bool(rec.get("is_restorable")),
                                                  "pid": rec.get("pid")}
                            if rec.get("cwd") and not pane.cwd:
                                pane.cwd = rec["cwd"]
                        ms.panes.append(pane)
        return ms

    # The tty is found through the environment cmux stamps on every process
    # it spawns; `tree` only reports it for surfaces that have been drawn.
    @staticmethod
    def _tty_expr(surface_id: str) -> str:
        return (f"ps -axE -o tty=,command= 2>/dev/null | grep {shlex.quote('CMUX_SURFACE_ID=' + surface_id)} "
                "| awk '$1 ~ /^tty/ {print $1; exit}'")

    def process_info_argv(self, pane: MuxPane) -> list[str] | None:
        if pane.extra.get("non_terminal"):
            return None
        return ["sh", "-c",
                f't=$({self._tty_expr(pane.pane_id)}); [ -n "$t" ] || exit 0; echo "tty=$t"; '
                'ps -t "$t" -o pid=,ppid=,stat=,args= 2>/dev/null; '
                'p=$(ps -t "$t" -o pid=,stat= 2>/dev/null | awk \'$2 ~ /\\+/ {print $1}\' | tail -1); '
                '[ -n "$p" ] && lsof -a -p "$p" -d cwd -Fn 2>/dev/null | sed -n "s/^n//p"; true']

    def parse_process_info(self, pane: MuxPane, stdout: str) -> dict:
        from .tmux import TmuxBackend
        lines = (stdout or "").splitlines()
        if lines and lines[0].startswith("tty="):
            pane.tty = lines[0][4:].strip() or pane.tty
            lines = lines[1:]
        # the wrapper `/bin/sh -c '… claude …'` and `login` rows are not the shell's own pid, so
        # the tmux parser (foreground = STAT `+`, own pid skipped) applies unchanged
        info = TmuxBackend.parse_process_info(self, pane, "\n".join(lines))   # type: ignore[arg-type]
        procs = [p for p in info["foreground_processes"] if p.get("name") not in ("login",)]
        # the cmux Claude wrapper runs `/bin/sh -c "claude …"` in front of the agent; the
        # agent row (the youngest) is the one that matters
        procs.sort(key=lambda p: p["pid"])
        info["foreground_processes"] = procs
        info["foreground_process_group_id"] = procs[-1]["pid"] if procs else None
        return info

    def excerpt_argv(self, pane_id: str, lines: int) -> list[str] | None:
        return ["read-screen", "--surface", pane_id, "--lines", str(lines)]

    def resume_binding_argv(self, surface_id: str) -> list[str]:
        return ["--json", "surface", "resume", "show", "--surface", surface_id]

    # -- mutation ----------------------------------------------------
    def send_text(self, pane_id: str, text: str) -> list[str]:
        return ["send", "--surface", pane_id, text]

    def clear_input(self, pane_id: str) -> list[str] | None:
        return ["send-key", "--surface", pane_id, "ctrl-u"]

    def send_enter(self, pane_id: str) -> list[str]:
        return ["send-key", "--surface", pane_id, "enter"]

    def interrupt(self, pane_id: str) -> list[str]:
        return ["send-key", "--surface", pane_id, "ctrl-c"]

    @staticmethod
    def _env(env: dict | None) -> list[str]:
        out: list[str] = []
        for k, v in sorted((env or {}).items()):
            out += ["--env", f"{k}={v}"]
        return out

    def workspace_create(self, label: str, cwd: str, window: str | None = None, env: dict | None = None) -> list[str]:
        # `workspace create --json` answers with workspace_id + surface_id (the root
        # surface); the legacy `new-workspace` prints only "OK workspace:N".
        # Workspace env is inherited by every shell cmux spawns there, restore included.
        return ["--json", "--id-format", "uuids", "workspace", "create", "--name", label, "--cwd", cwd,
                "--focus", "false", *self._env(env)]

    def tab_create(self, workspace_ref: str, label: str, cwd: str, env: dict | None = None) -> list[str] | None:
        # a Watchbill tab is a cmux pane (a split); env is workspace-scoped in cmux
        return ["--json", "--id-format", "uuids", "new-split", "right", "--workspace", workspace_ref, "--focus", "false",
                "--command", f"cd {shlex.quote(cwd)}"]

    def pane_split(self, pane_ref: str, direction: str, cwd: str, env: dict | None = None) -> list[str]:
        d = direction if direction in ("right", "down", "left", "up") else "right"
        return ["--json", "--id-format", "uuids", "new-split", d, "--surface", pane_ref, "--focus", "false",
                "--command", f"cd {shlex.quote(cwd)}"]

    def layout_apply(self, tab_ref: str, layout: str) -> list[str] | None:
        return None   # cmux restores its own layout on relaunch

    def pane_run(self, pane_ref: str, argv: Sequence[str], cwd: str) -> list[str]:
        return self.send_text(pane_ref, f"cd {shlex.quote(cwd)} && {shlex.join(argv)}")   # then send_enter

    def agent_start(self, name: str, kind: str, pane_ref: str, resume_argv: Sequence[str] | None,
                    flags: Sequence[str] | None = None) -> list[list[str]]:
        from ..classify import AGENT_KINDS
        argv = list(resume_argv) if resume_argv else [AGENT_KINDS.get(kind, kind), *(flags or [])]
        return [self.send_text(pane_ref, shlex.join(argv)), self.send_enter(pane_ref)]

    def agent_native_resume_wait(self, pane_ref: str, kind: str, resume_argv: Sequence[str] | None,
                                 timeout_ms: int) -> list[str]:
        """After a relaunch cmux resumes a hook-tracked agent itself. Wait for
        it; if nothing has appeared by a third of the timeout, type the
        recorded resume command (``fallback`` → the step counts as mutating)."""
        cmd = shlex.join(list(resume_argv)) if resume_argv else ""
        return [POLL, pane_ref, "agent", str(timeout_ms), kind or "", "fallback", cmd]

    def agent_wait_exit(self, pane_ref: str, kind: str | None, timeout_ms: int) -> list[str]:
        return [POLL, pane_ref, "shell", str(timeout_ms), kind or "", "nudge"]

    def agent_wait_idle(self, target: str, timeout_ms: int) -> list[str]:
        return [POLL, target, "idle", str(timeout_ms), ""]

    def agent_prompt(self, target: str, text: str) -> list[list[str]]:
        return [self.send_text(target, text), self.send_enter(target)]

    def agent_get(self, target: str) -> list[str] | None:
        return ["read-screen", "--surface", target, "--lines", "15"]   # blocked check reads the screen

    def workspace_close(self, ws_ref: str) -> list[str]:
        return ["close-workspace", "--workspace", ws_ref]

    def session_stop(self, session: str) -> list[str] | None:
        return None   # not a cmux verb: see session_stop_shell

    def session_stop_shell(self, session: str) -> list[str]:
        """Quit the app. Verified: with ``app.confirmQuit = "never"`` the
        AppleScript quit returns in ~2 s; with the default ``always`` it
        blocks on the dialog (AppleEvent timed out) and the app stays up."""
        app = shlex.quote(self.app)
        return ["sh", "-c",
                f"osascript -e 'with timeout of 15 seconds' -e 'tell application \"{self.app}\" to quit' "
                f"-e 'end timeout' >/dev/null 2>&1; "
                f"for i in $(seq 1 25); do pgrep -x {app} >/dev/null 2>&1 || exit 0; sleep 1; done; "
                f"echo {shlex.quote(QUIT_HINT)} >&2; exit 1"]

    def server_stop(self) -> list[str] | None:
        return None

    def session_start(self, session: str, first_label: str, cwd: str, window: str | None = None,
                      env: dict | None = None) -> list[str] | None:
        return None   # the host's start command (default `open -a cmux`) relaunches the app

    def server_up_poll(self, session: str, timeout_ms: int) -> list[str]:
        """Socket answers, then the restored tree has a surface. cmux rebuilds
        workspaces and resumes agents in the seconds after the socket opens."""
        cx = shlex.join(self.cli_prefix(session))
        n = max(2, timeout_ms // 1000)
        return ["sh", "-c",
                f"for i in $(seq 1 {n}); do {cx} ping 2>/dev/null | grep -q PONG && "
                f"{cx} --json tree --all 2>/dev/null | grep -q '\"surface_ref\"' && {{ sleep 3; exit 0; }}; sleep 1; done; exit 1"]

    def reload_config(self) -> list[str] | None:
        return ["reload-config"]   # verified: re-reads ~/.config/cmux/cmux.json in place

    def is_mutating(self, argv: Sequence[str]) -> bool:
        a = list(argv)
        if a[:1] in ([MANUAL], [POLL]) or a[:2] == ["sh", "-c"]:
            return False
        a = _strip_global(a)
        if not a:
            return False
        if a[0] in READONLY_NAMESPACED_HEADS:
            return not any(tuple(a[:len(k)]) == k for k in READONLY_NAMESPACED)
        return a[0] not in READONLY

    def created_ids(self, stdout: str) -> dict:
        s = (stdout or "").strip()
        out: dict = {}
        try:
            d = json.loads(s)
        except ValueError:
            d = None
        if isinstance(d, dict):
            if d.get("surface_id"):
                out["pane_id"] = str(d["surface_id"])
            if d.get("pane_id"):
                out["tab_id"] = str(d["pane_id"])
            if d.get("workspace_id"):
                out["workspace_id"] = str(d["workspace_id"])
            return out
        if s.startswith("OK "):   # legacy `new-workspace`: "OK workspace:N" (a positional ref)
            out["workspace_id"] = s[3:].split()[0]
        return out

    def agent_target(self, pane_id: str, name: str) -> str:
        return pane_id

    def wait_via(self) -> str:
        return "shell"

    def resolve_poll(self, session: str, argv: Sequence[str]) -> list[str]:
        """``POLL`` placeholder → one remote sh loop. Signals: ``$fg`` = the
        surface's foreground rows (``ps -t <tty>``), ``$lc`` = cmux's recorded
        lifecycle for the surface, ``$q`` = seconds the screen has not changed."""
        from ..classify import AGENT_KINDS
        a = list(argv)
        if a[:1] != [POLL]:
            return a
        _, ref, mode, timeout, kind, *extra = a
        exe = AGENT_KINDS.get(kind, kind) if kind else ""
        cx = shlex.join(self.cli_prefix(session))
        n = max(1, int(timeout) // 1000)
        q_ref = shlex.quote(ref)
        agent_re = shlex.quote(f"(^|[ /]){exe}( |$)") if exe else "'^$'"
        read = (f't=$({self._tty_expr(ref)}); '
                'fg=""; [ -n "$t" ] && fg=$(ps -t "$t" -o stat=,args= 2>/dev/null | awk \'$1 ~ /\\+/\' | cut -c1-200); '
                'last=$(printf "%s\\n" "$fg" | tail -1 | awk \'{print $2}\' | sed "s#.*/##; s#^-##"); '
                f'ag=0; printf "%s\\n" "$fg" | grep -q -E {agent_re} && ag=1')
        is_shell = "case \"$last\" in " + "|".join(SHELLS) + ") sh=1;; *) sh=0;; esac"
        if mode == "shell":
            cond = '[ "$ag" = 0 ] && [ "$sh" = 1 ]'
            # codex: the first Enter after /quit can land while its slash popup opens
            # and only complete the command; press Enter once more (rehearsal, 2026-09-13)
            poke = (f'; [ "$i" = 4 ] && {cx} send-key --surface {q_ref} enter >/dev/null 2>&1' if "nudge" in extra else "")
            body = f'{read}; {is_shell}; {cond} && exit 0{poke}'
        elif mode == "agent":
            cmd = extra[extra.index("fallback") + 1] if "fallback" in extra else ""
            fb = ""
            if cmd:
                k = max(2, n // 3)
                fb = (f'; [ "$i" = {k} ] && [ "$ag" = 0 ] && {{ {cx} send --surface {q_ref} {shlex.quote(cmd)} >/dev/null 2>&1; '
                      f'{cx} send-key --surface {q_ref} enter >/dev/null 2>&1; }}')
            body = f'{read}; {is_shell}; [ "$ag" = 1 ] && [ "$sh" = 0 ] && exit 0{fb}'
        elif mode == "idle":
            # cmux's lifecycle is authoritative when it has one; a freshly
            # resumed agent reads `unknown` until its first turn, so fall back
            # to "agent in the foreground and the screen quiet for a while"
            lc = (f'lc=$({cx} --json sessions list --surface {q_ref} 2>/dev/null | '
                  'sed -n \'s/.*"agent_lifecycle" *: *"\\([a-zA-Z]*\\)".*/\\1/p\' | head -1)')
            scr = f'h=$({cx} read-screen --surface {q_ref} --lines 12 2>/dev/null | cksum | cut -d" " -f1)'
            quiet = 'if [ "$h" = "$hp" ]; then q=$((q+1)); else q=0; fi; hp=$h'
            # `needsInput` also follows Claude's idle notification, so it is not
            # trusted on its own: a quiet screen with no dialog pattern is idle
            pats = shlex.quote(r"Do you want to|esc to cancel|\[y/n\]|\(y/n\)|\[Y/n\]|\[y/N\]|Press Enter to continue|❯ *1\. *Yes")
            blk = f'blk=$({cx} read-screen --surface {q_ref} --lines 12 2>/dev/null | grep -c -E {pats} || true)'
            cond = (f'{{ [ "$lc" = idle ] || [ "$lc" = ended ]; }} && [ "$sh" = 0 ] && exit 0; '
                    f'[ "$lc" != running ] && [ "$lc" != launching ] && [ "$sh" = 0 ] '
                    f'&& [ -n "$fg" ] && [ "$q" -ge {int(self.idle_after_s)} ] && [ "${{blk:-0}}" = 0 ] && exit 0')
            body = f'{read}; {is_shell}; {lc}; {scr}; {quiet}; {blk}; {cond}'
        else:
            raise ValueError(f"unknown cmux poll mode {mode!r}")
        return ["sh", "-c", f'q=0; hp=""; for i in $(seq 1 {n}); do {body}; sleep 1; done; exit 1']


READONLY_NAMESPACED_HEADS = {k[0] for k in READONLY_NAMESPACED}
