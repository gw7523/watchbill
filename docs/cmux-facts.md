# cmux facts Watchbill relies on

Verified live on the Mac mini against **cmux 0.64.22 (102)** over SSH on
2026-09-14, with a throwaway Claude in a throwaway workspace. The earlier
docs-only version of this file (from the published CLI reference) is gone:
the installed CLI differs from it in almost every verb Watchbill needs, and
everything below was run, not read. `cmux --help`, `cmux docs api`
(`docs/cli-contract.md` in `manaflow-ai/cmux`) and
`docs/agent-hooks.md` are the references; the CLI is the authority.

## Model and identity

| cmux | Watchbill roster slot |
|---|---|
| the running app (one per user, GUI) | `session` (the name in hosts.toml is cosmetic; `app` by convention) |
| window → workspace (title, `current_directory`) | `workspace_label` |
| pane (a split inside a workspace) | `tab_label` = `pane<n>` |
| surface (a terminal or browser tab inside a pane; `CMUX_SURFACE_ID`) | `pane_label` = `s<n>`, `live_ids.pane_id` = surface UUID |

Handles: every command takes a UUID, a ref (`workspace:2`, `surface:1`) or
an index. **Refs are positional and renumber** whenever a workspace closes
or the app relaunches (the probe workspace was `workspace:2` before a
relaunch and `workspace:1` after). Watchbill stores and targets UUIDs only
(`--id-format uuids|both`). Workspace and surface UUIDs survive a quit and
relaunch verbatim; pane UUIDs are re-minted (as `docs/agent-session-
tracking-spec.md` says: the surface id is the durable binding key).

## Socket and auth

Socket: `~/.local/state/cmux/cmux.sock` (`last-socket-path` next to it).
Auth: `--password`, then `CMUX_SOCKET_PASSWORD`, then the password saved in
Settings — and the CLI run as the same user reads the saved one itself, so
**no password is passed over SSH or recorded anywhere**. `cmux ping` →
`PONG` iff the app answers; `cmux version` prints the CLI's own version
(`cmux 0.64.22 (102) [ddd4a01bc]`), so it is not a liveness test.
`CMUX_QUIET=1` silences alias notices such as `list-workspaces is now an
alias for workspace list`.

## Listing (read-only)

| verb | gives |
|---|---|
| `--json --id-format both tree --all` | windows → workspaces (id, ref, title, index) → panes (id, ref) → surfaces (id, ref, title, type, tty). `tty` is **null for a surface that has not been drawn**, so it is not relied on. |
| `--json --id-format both workspace list` | `current_directory` per workspace (env is deliberately omitted from this listing). |
| `--json --id-format uuids top --all --processes` | pids attributed per surface (`attributions[].surface_id`, `reason: surface-process-tree`; the shell is the oldest) and `coding_agents[]` (kind + pids) — ~35 KB for two workspaces. |
| `--json sessions list --all` | the agent hook store: `agent`, `agent_lifecycle`, `session_id`, `surface_id`, `workspace_id`, `pid`, `cwd`, `is_restorable`, `launch_arguments`, `active_for_surface`. Works without the socket. `--surface <uuid>` filters. |
| `--json surface resume show --surface S` | `restore_record.prepared_arguments` = the exact command cmux runs on relaunch (`claude --resume <id> --model haiku`), `restore_record.kind`, `resume_binding.auto_resume`, `permission_mode`. `null` until the agent's first hook fires. |
| `read-screen --surface S --lines N` | the visible screen (also `capture-pane`, the tmux-compat alias). |
| `--json workspace env --workspace W` | the per-workspace environment cmux injects (`count`, `env`). |
| `capabilities`, `identify`, `list-panes`, `list-pane-surfaces` | as named; `list-surfaces` does not exist. |

Process info therefore comes from the process table, as on tmux: every
process cmux spawns carries `CMUX_SURFACE_ID=<uuid>` in its environment.
`ps -axE -o tty=,command= | grep CMUX_SURFACE_ID=<uuid> | awk '$1 ~ /^tty/'`
finds the surface's tty (some matches have tty `??`; the first `ttys…` row
wins), then `ps -t <tty> -o pid=,ppid=,stat=,args=` lists what runs on it
and `lsof -p <pid> -d cwd` gives the cwd. The rows for a Claude surface:
`login` (Ss), the shell (S), and the agent (S+). The cmux Claude wrapper
starts the agent as `claude --session-id <uuid> --settings {hooks…} --model
…`, so the resume tables must drop `--session-id` (they do).

## Agent hooks: detection, status, resume — all native

The **Claude Code integration** (Settings; on by default here) wraps
`claude`: it injects `--session-id` and a `--settings` JSON whose hooks
call `cmux hooks claude <event>`. Other agents get hooks from `cmux hooks
setup` (codex, grok, opencode, gemini, cursor, … see `docs/agent-hooks.md`).
The hooks write `~/.cmuxterm/<agent>-hook-sessions.json`:

| field | Watchbill use |
|---|---|
| `sessionId`, `surfaceId`, `workspaceId`, `cwd`, `pid` | `agent_session.value`, the pane it lives in |
| `agentLifecycle`: `unknown` → `idle` (SessionStart / Stop) → `running` (UserPromptSubmit) → `needsInput` (Notification / PermissionRequest) | `agent_status`: unknown / idle / working / blocked. **A freshly resumed agent reads `unknown` until its first turn**, and **`needsInput` also follows Claude's idle notification** (a minute after a reply, no dialog on screen), so Watchbill downgrades it to `idle` unless the screen shows an approval pattern. |
| `isRestorable`, `launchCommand` (sanitized: model/config flags kept, prompts and credentials dropped) | what cmux will replay |
| `lastPermissionMode` | recorded permission mode |

Lifecycle observed live: `unknown` at start (trust dialog pending) → `idle`
after the first reply → `needsInput` on an idle notification → `unknown`
again after a relaunch until the next turn. The store is read through
`cmux sessions list`, never parsed from disk by Watchbill.

## Relaunch restores everything, including the agents

Verified three times (SIGTERM once, AppleScript quit twice):

1. quit or kill the app → every terminal dies with it (the agent process is
   gone within a second);
2. `open -a cmux` **from an ssh shell** relaunches it in the logged-in
   desktop session (socket answers after ~2 s; keychain, hooks and
   notifications work — unlike an ssh-started herdr or tmux server on macOS);
3. cmux rebuilds every workspace with the **same workspace and surface
   UUIDs**, then runs each restorable binding's `prepared_arguments` itself:
   `claude --resume <id> --model haiku`, new pid, same session id;
4. the resumed Claude recalled the codeword from before the relaunch.

This holds **after a graceful `/exit` too**: the binding stays
`isRestorable: true`, `auto_resume: true`, and the relaunch resumes the
parked agent. Consequence for Watchbill: in any window that stops the app,
the restore phase *waits* for cmux's own resume (`agent_native_resume_wait`:
agent process in the surface's foreground) and only types the recorded
resume command itself when nothing has appeared after a third of the wait.
Typing it unconditionally would land inside the already-running agent.
Turn the behaviour off in cmux with **Settings > Terminal > Resume Agent
Sessions on Reopen** (`terminal.autoResumeAgentSessions: false`); the
fallback then starts the agents.

## Quitting the app from a script

`osascript -e 'tell application "cmux" to quit'` from ssh works — **only
with `app.confirmQuit = "never"`** in `~/.config/cmux/cmux.json` (applied
live by `cmux reload-config`). With the default `always` the quit hangs on
the confirmation dialog (`AppleEvent timed out (-1712)`), a second quit
while that dialog is up is answered `User canceled (-128)`, and the app
stays running. `kill -TERM <pid>` also works (the session snapshot is kept
continuously, so restore was complete) but skips the app's own shutdown;
Watchbill uses the AppleScript quit and fails the step with the config hint
when the app is still alive after 25 s. `doctor` warns when the setting is
not `never`. `warnBeforeClosingTab = false` likewise lets `close-workspace`
close a workspace whose terminal has a running process without a dialog.
No socket method quits or relaunches the app (`capabilities` lists 303
methods; `app.*` has only focus overrides).

Owner's Mac mini, 2026-09-14: `~/.config/cmux/cmux.json` carries
`"app": { "confirmQuit": "never", "warnBeforeClosingTab": false,
"warnBeforeClosingTabXButton": false }` (the pristine template is next to
it as a timestamped `.bak`).

## Input

| verb | verified |
|---|---|
| `send --surface S <text>` | literal text (`/exit`, prompts with spaces) |
| `send-key --surface S <key>` | `enter`, `escape`, `ctrl-c` (→ `^C`), `ctrl-u`, `cmd-c` accepted; `C-c`, `^C`, `c-u` → `Unknown key` |
| `/exit` typed into Claude | agent gone in ~1 s; the surface's shell survives (`Resume this session with: claude --resume …`) |

## Creating and closing

| verb | verified |
|---|---|
| `--json --id-format uuids workspace create --name L --cwd D --focus false [--env K=V …]` | JSON `{workspace_id, surface_id, window_id}`; env is inherited by every shell in the workspace and re-applied on restore |
| `new-workspace …` (legacy) | same, but prints only `OK workspace:N` (a ref), even with `--json` |
| `--json --id-format uuids new-split right --surface S --focus false [--command …]` | JSON `{pane_id, surface_id, …}`; `--command` is typed into the new interactive shell with one Enter |
| `close-workspace --workspace W` | `OK workspace:N`; needs `warnBeforeClosingTab = false` when a process runs there |
| `select-workspace --workspace W` | changes the app's selection (visible on the user's screen: avoided) |
| `reload-config` | `OK Reloaded config`; re-reads `cmux.json` and the Ghostty config in place |

Watchbill's shape rebuild rarely runs on cmux: after a relaunch every
workspace is already back with its UUIDs, so the create steps are skipped by
the reuse hints. A Watchbill tab (cmux pane) is recreated as a right split
of the workspace; extra surfaces inside one pane are not recreated.

## Absent or unused

`agent-hibernation on|off` (opt-in; kills idle background agents and
resumes them on focus — off on this Mac), `restore-session` (restore the
previous saved session into a running app; not needed, relaunch does it),
`local-tmux` (cmux surfaces attached to a tmux server under
`~/.cmux/local-tmux`; a tmux-backed cmux would be a `mux = "tmux"` host),
`vault`/`fork`, notifications, todo, browser, Cloud VMs. No plugin system,
no live handoff on upgrade. `brew upgrade --cask cmux` remains
UNVERIFIED-LIVE.
