# cmux facts Watchbill relies on

cmux (cmux.com, `manaflow-ai/cmux`) is a native macOS Swift/AppKit terminal
multiplexer for coding agents. It is **not** installable on the Linux
cockpit, so every fact here is **docs-verified** (CLI reference at
`https://cmux.com/docs/api`, README, fetched 2026-09-11) and marked
UNVERIFIED-LIVE until probed on a Mac. Nothing below was invented; anything
the docs do not say is listed as absent.

## Model

| cmux | Watchbill roster field |
|---|---|
| app instance (one per user, GUI) | `session` = `"app"` |
| workspace (`CMUX_WORKSPACE_ID`) | `workspace_label` (rename via ⌘⇧R; list gives id + title) |
| panel (a split) | `tab_label` (`panel<n>`; docs call splits "panels") |
| surface (`CMUX_SURFACE_ID`; a terminal or browser tab inside a panel) | `pane_label` (`s<n>`), `live_ids.pane_id` = surface id |

Identifiers come back as refs or uuids (`--id-format refs|uuids|both`).
Watchbill stores uuids in `live_ids` and refs nowhere.

## Socket (docs-verified)

`/tmp/cmux.sock` (release) or `/tmp/cmux-debug.sock`; `CMUX_SOCKET_PATH`
overrides. Newline-terminated JSON-RPC:

```
{"id":"req-1","method":"workspace.list","params":{}}
{"id":"req-1","ok":true,"result":{...}}
```

Access modes: `off`, "cmux processes only" (default: only processes spawned
inside cmux terminals may connect), `allowAll`. **Consequence:** a Watchbill
run over SSH is not a cmux-spawned process; the Mac must be set to
`allowAll` or Watchbill's remote command must be launched from a cmux
surface. The default mode makes remote `roll` fail closed, which is
acceptable; doctor reports it.

## CLI (docs-verified)

```
cmux list-workspaces [--json]          cmux new-workspace
cmux select-workspace --workspace ID   cmux close-workspace --workspace ID
cmux current-workspace [--json]
cmux list-panels [--json]              cmux list-pane-surfaces [--json]
cmux new-split left|right|up|down      cmux focus-panel --panel ID
cmux send [--surface ID] "text"        cmux send-key [--surface ID] enter|tab|escape|backspace|delete|up|down|left|right
cmux notify --title T --body B [--subtitle S]
cmux surface resume set|show --json|clear     # per-surface resume command binding
cmux hooks setup [codex | --agent opencode]   # installs agent resume hooks
cmux restore-session                          # re-apply the last saved snapshot
cmux local-tmux | ssh-tmux | mosh-tmux        # tmux-owned persistence variants
cmux ping | capabilities [--json] | identify [--json]

# sidebar / notification surface (documented; Watchbill only reads these)
cmux list-notifications [--json]   cmux clear-notifications
cmux notify --title T --body B [--subtitle S]
cmux set-status <key> <value> [--icon --color --priority --workspace]
cmux clear-status <key> | list-status | set-progress <0.0-1.0> --label T | clear-progress
cmux log "msg" [--level ...] | clear-log | list-log [--limit N] | sidebar-state [--workspace ID]
```

Global flags: `--socket PATH`, `--json`, `--window ID`, `--workspace ID`,
`--surface ID`, `--id-format refs|uuids|both`.

Environment injected into cmux terminals: `CMUX_WORKSPACE_ID`,
`CMUX_SURFACE_ID`, `CMUX_SOCKET_PATH`, `CMUX_SOCKET_MODE`,
`TERM_PROGRAM=ghostty`, `TERM=xterm-ghostty`.

## Persistence

Quitting cmux saves the session; relaunch restores window/workspace/pane
layout, working directories, scrollback (best effort), browser state. It
"does not checkpoint arbitrary live process state" — agents are gone after
a quit, exactly the Herdr cold-restart situation. `cmux surface resume
set/show` is cmux's own answer: a per-surface resume command that
`cmux hooks setup` wires for Claude Code, Codex and OpenCode. That binding
is the closest thing to Herdr's `agent_session`, and Watchbill reads it
(`surface resume show --json`) as the resume source of truth on cmux.

## Absent from the docs (fail closed until probed)

- Whether `list-panels` lists *panels* or *surfaces*: the published reference
  describes it as "List all surfaces in the current workspace", which sits
  badly against panels→tabs. **Probe before trusting the tab/pane split.**
- Any command that lists a surface's **cwd, pid, or foreground process**.
  `list-pane-surfaces --json` field names are unknown. Until probed, cmux
  occupants get `role` from the resume binding (agent) or `shell`, cwd from
  the restore snapshot if exposed, else empty.
- Any **screen read** (`capture`-like) → no excerpts on cmux.
- Any **agent status** → `unknown`; `secure` needs `--force` unless the
  resume binding says the surface is an agent that is idle (not knowable).
- A headless or daemon mode, or a CLI to quit/relaunch the app. `restart-harness`
  on cmux is documented as a **manual** step (quit the app, relaunch,
  `cmux restore-session`) that Watchbill waits on, never performs.
- Kill-server semantics: there is no server; "dismiss" is `close-workspace`.
- Live config reload, plugin install: absent. `reload-config` and
  `install-plugin` refuse on cmux.
