# Herdr 0.8.2 facts Watchbill relies on

Recorded on a live Omarchy box (`herdr 0.8.2-1` from pacman, protocol 20,
schema_version 1) on 2026-09-11. Everything marked **verified** was read from
`herdr <cmd> --help`, `herdr api schema --json`, `herdr api snapshot`, or the
agent CLI's own `--help`. Everything marked **UNVERIFIED-0.8.2** is a guess
with a fallback path and must be probed before MVP.

## CLI surface (verified)

| Command | Notes |
|---|---|
| `herdr --session <name> <group> <sub>` | Global `--session` selects the socket. Socket order: `--session` → `HERDR_SOCKET_PATH` → `HERDR_SESSION` → `~/.config/herdr/herdr.sock`. Named sessions live at `~/.config/herdr/sessions/<name>/herdr.sock` (seen in `session list`). |
| `herdr status server --json` | `{"status","running","version","protocol","capabilities":{"live_handoff","detached_server_daemon"},"compatible","socket","session","restart_needed"}`. **`live_handoff` is `true` on a pacman install.** Doctor must not trust it alone (see below). |
| `herdr api snapshot` | `result.snapshot` has `agents[]`, `panes[]`, `tabs[]`, `workspaces[]`, `layouts[]`, `focused_*`, `protocol`, `version`. Pane objects carry `agent`, `agent_session{agent,kind,source,value}`, `agent_status`, `cwd`, `foreground_cwd`, `pane_id`, `tab_id`, `workspace_id`, `terminal_id`, `terminal_title(_stripped)`, `scroll`. No `command`, no argv. |
| `herdr api schema --json` | Message-envelope schema; method names appear as `const`s. Relevant: `session.snapshot`, `layout.export`, `layout.apply`, `layout.set_split_ratio`, `pane.process_info`, `pane.run` (via CLI `pane run`), `agent.start`, `agent.prompt`, `agent.wait`, `server.stop`, `server.live_handoff`, `plugin.*`. |
| `herdr pane process-info --pane <id>` | `result.process_info{foreground_process_group_id, foreground_processes[{argv,cmdline,cwd,name,pid}], pane_id, shell_pid}`. This is the argv source of truth. |
| `herdr pane layout --pane <id>` | Works. `--current` exists but is wrong (#2297) — never used. |
| `herdr pane run <PANE_ID> <COMMAND>...` | Argv form, no shell. |
| `herdr pane send-keys <PANE_ID> <KEY>...` / `agent send-keys <TARGET> <KEY>...` | `esc` is the canonical Escape name. |
| `herdr pane read <PANE_ID> [--source visible\|recent\|recent-unwrapped\|detection] [--lines N] [--format text\|ansi]` | Excerpt source for `snap`. |
| `herdr pane close <pane_id>` | Mutating. |
| `herdr agent list` | JSON `result.agents[]`, same fields as snapshot panes minus `scroll`. No `--json` flag needed. |
| `herdr agent start <NAME> --kind <KIND> --pane <ID> [--timeout MS] [-- AGENT_ARG...]` | Kinds: pi, claude, codex, gemini, cursor, devin, agy, cline, omp, mastracode, opencode, copilot, kimi, kiro, droid, amp, grok, hermes, kilo, qodercli, qwen, maki. Pane must be at an interactive shell prompt. Returns after readiness (default 30 s, max 300 s). |
| `herdr agent prompt <TARGET> <TEXT> [--wait] [--until STATE]... [--timeout MS]` | **Rejected with `agent_blocked` when the agent is blocked, before any input is sent.** `agent_prompt_stalled` if no state change within 5 s after `--wait`. |
| `herdr agent wait <TARGET> [--until STATE]... [--timeout MS]` | Default matches idle, done, blocked. |
| `herdr session list [--json]` / `session stop [--json] <NAME>` / `session attach <NAME>` / `session delete <NAME>` | `session stop default` stops the default session's server. It is not `server stop`. |
| `herdr server stop` | Stops the running server via the API socket. Watchbill never emits it without `--force-server-stop`. |
| `herdr workspace create [--cwd PATH] [--label TEXT] [--env K=V] [--focus\|--no-focus]` / `workspace close <id>` / `workspace list` | Labels are the durable half of `human_id`. Success result carries `workspace`, `tab`, `root_pane`. |
| `herdr tab create [--workspace ID] [--cwd PATH] [--label TEXT] [--env K=V] [--focus\|--no-focus]` | Extra tabs in a restored workspace. |
| `herdr pane split [PANE_ID] [--pane ID] [--current] [--direction right\|down] [--ratio F] [--cwd PATH] [--focus\|--no-focus]` | Positional pane id is used; `--current` never. |
| `herdr pane send-text <PANE_ID> <TEXT>` / `herdr agent get <target>` | Park input and the pre-prompt blocked check. |
| `herdr tab list --workspace <id>` | Tabs carry `label` and `number`. |
| `herdr plugin install [--ref REF] [-y\|--yes] <OWNER/REPO[/SUBDIR]>` / `plugin list` / `plugin link [--disabled\|--enabled] <PATH>` / `plugin enable\|disable <ID>` / `plugin action list\|invoke` | `plugin list` is human text in 0.8.2 (`- <id> (<name>) enabled [local:/path]`); `plugin action list` is JSON. |
| `herdr integration install <TARGET>` / `integration status` | Targets include claude, codex, cursor, opencode, hermes, grok. `status` prints `<kind>: current (vN) (<path>)` or `not installed`. |
| `herdr update [--handoff]` | Official-installer updater. Never emitted on pacman/mise/brew/nix hosts. |
| `herdr --remote <ssh-target> [--session <name>] [--handoff]` | Exists. Not the default transport: version matching breaks on Omarchy packages. |

## Plugin manifest (verified, from a linked local plugin)

```toml
id = "personal.open-url"
name = "Open URL"
version = "0.2.0"
min_herdr_version = "0.8.2"
description = "..."
platforms = ["linux"]

[[actions]]
id = "open"
title = "Open URL"
command = ["bash", "open.sh"]
```

`[[link_handlers]]` also exists. `[[startup]]` hooks are referenced by the
build contract; their exact table shape is **UNVERIFIED-0.8.2** (no local
plugin uses one). Watchbill only *reads* a target plugin's manifest to decide
whether `restart-harness` must follow `install-plugin`; the parse is tolerant
(any `startup` key → assume hooks).

## Native agent resume argv

| kind | argv | verified on |
|---|---|---|
| claude | `claude --resume <id>` | Claude Code 2.1.267 `--help`: `--resume <session-id>`; `-c/--continue` exists as fallback |
| grok | `grok --resume <id>` (fallback `--continue`) | grok 1.0.25 `--help`: `-r, --resume [<SESSION_ID_OR_TITLE>]`, `-c, --continue` |
| codex | `codex resume <id>` | codex-cli 0.153.4 `resume --help`: `codex resume [SESSION_ID] [PROMPT]`, `--last` |
| cursor | `cursor-agent --resume <id>` | cursor-agent 2026.09.10: `--resume [chatId]`, `--continue` |
| opencode | `opencode --session <id>` | opencode 1.18.29 `--help`: `-s, --session <id>`, `-c, --continue` |
| hermes | `hermes --resume <id>` | **UNVERIFIED-0.8.2** (binary was mid-install when probed); contract cites the official integration |
| gemini | `gemini --resume <id-or-index>` | gemini 0.59.0 `--help`: `-r, --resume`; not in contract table, included as opportunistic |

`agent_session.value` in the snapshot is the id these flags take
(`source: herdr:<kind>`). Integration status on the probe box: claude, codex,
cursor, opencode, hermes, grok, pi are `current`.

## Live handoff and install flavor

`herdr status server --json` on the pacman box reports
`capabilities.live_handoff: true`. The contract says handoff only works for
official `curl | sh` installs. Doctor therefore computes
`handoff_supported = flavor == "official" and capabilities.live_handoff`, where
flavor comes from the binary path and package owner:

| probe | flavor |
|---|---|
| `pacman -Qo $(command -v herdr)` succeeds | `pacman` (Omarchy) |
| path under `~/.local/share/mise/` | `mise` |
| path under `/opt/homebrew/` or `/usr/local/Cellar/` or `brew --prefix` | `brew` |
| path under `/nix/store/` | `nix` |
| path is `~/.local/bin/herdr` or `/usr/local/bin/herdr` and no package owner | `official` |
| anything else | `unknown` → handoff unsupported |

## Learned in the live rehearsal (2026-09-13)

An isolated `wbrehearse` session on rig2 with one real haiku Claude was parked,
stopped, restarted and resumed twice through `watchbill relieve
restart-harness --yes`. Both times the agent came back on the same session id
with the same flags and answered a question only the original conversation
could.

| Fact | Consequence in Watchbill |
|---|---|
| `herdr --session S server` stays in the **foreground** | default start is `setsid -f … </dev/null >/dev/null 2>&1` |
| `status server --json` exits **0** for a stopped session (`"running":false`) | the start wait polls for `"running":true` |
| `agent start … -- <args>` **prepends the kind's executable** (`-- claude --model x` ran `claude claude --model x`) | only arguments go after `--` |
| a session restarted after `session stop` **restores its workspace layout** from `session.json` (panes come back as shells in the recorded cwd) | create steps reuse a restored pane by workspace label, tab label and order |
| `agent start` and `agent start … -- --resume <id>` both work with **no client attached** | issue #2064 did not reproduce on this path; the viewport step stays until it is also shown unnecessary over SSH and for other kinds |
| a server started from inside a Claude session inherits `CLAUDECODE` / `CLAUDE_CODE_CHILD_SESSION`; agents in it write **no transcript** and cannot be resumed | the local transport scrubs agent-session environment from everything it spawns |
| Claude's folder-trust dialog blocks `agent start` (`agent_not_ready`, status `blocked`) | recorded config includes `trusted`; losing trust stops the host before the agent starts |

### Learned on ser6 over SSH (2026-09-13)

A throwaway session on ser6 (haiku Claude + Grok) was cycled three times over
SSH, then ser6's real `default` session (two Claude agents and a Grok) was
cycled twice. Every agent came back on its own session id with its own flags.

| Fact | Consequence in Watchbill |
|---|---|
| Grok's `/exit` exits the TUI (documented in grok's README, now verified live) | Grok park is verified |
| Grok reports `agent_session` only after its first prompt | an idle Grok that was never prompted is `unref` and resumes fresh |
| a Grok just resumed with a long history can report idle while still redrawing, and drop a submitted prompt (`agent_prompt_stalled`) | exec settles and retries a stalled prompt once |
| Claude's trust dialog appears for a folder with **no** `projects` entry, even with `--dangerously-skip-permissions` (rig2 2.1.267, ser6 2.1.252 in `/tmp/wb-hooktest`); a folder with an explicit `hasTrustDialogAccepted: false` starts without it (ser6 `omarchy-plugins`) | recorded `trusted` is informative, not predictive; only a change from true to false stops a host |
| a herdr server started over ssh inherits `SSH_CONNECTION` and lacks the desktop session (`WAYLAND_DISPLAY`, `DISPLAY`, `XDG_SESSION_TYPE=wayland`) | server env is recorded and restored; the live systemd user environment is authoritative; SSH_* is dropped |
| the server is the parent process of every pane shell | how Watchbill finds and reads a herdr server's environment |
| a server started by a correct systemd user unit (`personal-config` `tools/linux/herdr-autostart`) gets the desktop session from the unit itself; Watchbill's `start = "systemctl --user start herdr.service"` hands the server back to systemd so it survives a reboot (ser6, 2026-09-13) | prefer a host's own unit as `start` where one exists |
| a machine can run a second, independent herdr for a seat (ser6's SFL distrobox runs its own `~/Work/sfl/.home/.local/bin/herdr server`) | stopping one herdr session never touches the other; name hosts by the herdr they talk to |

## UNVERIFIED-0.8.2 list (probe before MVP)

1. ~~Headless session start~~ — verified locally (`setsid -f herdr --session S server`). Still to prove over SSH on ser6.
2. Whether any client attach exists that does not need a TTY. Assumed no: `set` attaches a viewport by running `ssh -tt <target> herdr session attach <name>` in a cockpit pane (#2064).
3. `layout.export` / `layout.apply` CLI exposure, and the `splits` tree format for multi-pane tabs. They exist as socket methods (schema consts) but no `herdr layout` CLI group exists in 0.8.2. Watchbill's `ssh_cli` transport therefore rebuilds shape with `workspace create` + `pane split` (both verified CLI) and only uses `layout.apply` through a socket transport.
4. `[[startup]]` table shape in `herdr-plugin.toml`.
5. `hermes --resume <id>`.
6. Whether `herdr agent list` exposes the name set by `agent rename` (no `name` field was present on unnamed agents in the snapshot).
7. `send-keys` key names beyond `esc`: `enter`, `ctrl-c`.
8. `agent wait <pane-id> --until unknown` as the "agent exited" signal after `/exit`.
9. Agent self-update subcommands: `grok upgrade`, `cursor-agent update`, `opencode upgrade`.
