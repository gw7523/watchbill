# Multiplexer backends: Herdr, tmux, cmux

*Design note for the second architecture pass (2026-09-11). This changes the
locked module map, so it is also the one-page dissent the contract asks for.*

## Why

The first pass hard-wired Herdr. The owner's fleet is mixed: Omarchy boxes
run Herdr, the Macs run tmux (and cmux is the candidate agent cockpit on
macOS). The burden Watchbill removes — knowing what is running where,
standing it down cleanly, bringing it back with each agent's conversation,
and wrapping plugin installs, upgrades and harness restarts around that — is
the same on all three. Only the verbs differ.

## Decision

Introduce a **mux backend** axis, orthogonal to transport:

```
transport  = how to reach the host            local | ssh_cli | ssh_socket | herdr_remote
mux        = which multiplexer owns the PTYs  herdr | tmux | cmux
```

`hosts.toml` gains `mux = "tmux"` per host (default `herdr`). A host has one
mux. The roster gains an optional `mux` field on hosts and occupants,
default `herdr`; **schema stays 1** (rule: optional fields with defaults are
additive inside a schema version; a bump is for changed or removed required
fields).

The five-part `human_id` is unchanged. Each backend maps its own nouns onto
`host/session/workspace/tab/pane`:

| slot | herdr | tmux | cmux |
|---|---|---|---|
| session | named session (`--session S`) | server socket (`-L S`) | the app (name cosmetic; `app`) |
| workspace | workspace label | session name | workspace title |
| tab | tab label | window name (index) | pane, i.e. a split (`pane<n>`) |
| pane | agent name / `p<n>` | `p<pane_index>` | surface (`s<n>`) |
| live pane id | `w1:p2` | `%N` | surface uuid |

## Module changes

```
src/watchbill/mux/
  base.py      MuxBackend protocol, Capabilities, MuxSnapshot (backend-neutral
               workspaces/tabs/panes), argv builders, output parsers
  herdr.py     the verbs the first pass used, unchanged in behaviour
  tmux.py      tmux 3.x (verified on 3.7c; docs/tmux-3.7-facts.md)
  cmux.py      cmux 0.64 (verified live on the Mac mini; docs/cmux-facts.md)
src/watchbill/detect.py   agent role/kind/status heuristics for muxes that
               do not know agents (argv, quiet time, approval-prompt patterns)
```

Planners no longer spell `herdr …`. They ask the host's backend for argv
(`backend.send_text(pane, text)` → argv) and for classification
(`backend.is_mutating(argv)`). `exec` runs `hs.mux(*raw)`; the transport
prefixes the backend's CLI (`herdr --session S`, `tmux -L S`,
`cmux --socket P`). Collect asks the backend to parse its own listing output
into a `MuxSnapshot`; `build_roster` is unchanged above that.

## Capability matrix

| capability | herdr | tmux | cmux |
|---|---|---|---|
| agent detection (role/kind) | native | derived from argv | native: agent hook store (`sessions list`) + argv from `ps -t` |
| agent status idle/working/blocked | native | derived: quiet time + prompt patterns → idle / working / unknown | native: hook lifecycle (idle / running / needsInput; `unknown` until a resumed agent's first turn) |
| native resume id | `agent_session` | none → cwd-scoped `--continue` with cwd-uniqueness guard | hook store session id; cmux replays the resume itself on relaunch |
| process info (cwd, argv) | `pane process-info` | `ps -t <pane_tty>` | `ps -t <tty>` (tty from `CMUX_SURFACE_ID` in the process env) |
| screen excerpt | `pane read` | `capture-pane -p` | `read-screen --surface` |
| shape rebuild | workspace/tab create + split | new-session/new-window/split + `select-layout '<layout>'` (exact) | `workspace create` + `new-split`; rarely needed — the app restores its own layout on relaunch |
| live config reload (PTYs kept) | `server reload-config` | `source-file` | `reload-config` |
| live handoff on binary upgrade | official installer only | no | no |
| headless start | UNVERIFIED (`herdr --session S server`) / systemd unit | `new-session -d` | `open -a cmux` from ssh (lands in the desktop session; verified) |
| plugin install | `plugin install` (+ bounce if startup hooks) | TPM `install_plugins` + `source-file` (live) | none (no plugin system) |
| viewport needed for resume (#2064) | yes | no | n/a (GUI) |
| "server stop" guard | `server stop` needs `--force-server-stop` | `kill-server` needs `--force-server-stop` | the AppleScript quit needs `--force-server-stop` unless planned; needs `app.confirmQuit = "never"` |

Planners consult `Capabilities` and turn a missing capability into a
`Refusal` (exit 3) or a `note`, never into a guess:

- `relieve --mode live` on tmux/cmux → refused (no handoff).
- `reload-config` is a live action on every mux (herdr `server reload-config`,
  tmux `source-file`, cmux `reload-config`): nothing parked, nothing stopped.
- `install-plugin` on cmux → refused (no plugin system); on tmux it is live (TPM + source-file).
- on cmux, a window that stops the app waits for cmux's own agent resume
  (it replays `claude --resume <id> …` itself on relaunch) and types the
  recorded command only if none appears.
- `secure park` of an agent whose status is `unknown` → refused without
  `--force` (same rule as `working`: we cannot prove it is safe to type `/exit`).

## Agent identity without a mux that knows agents

`detect.py`, used by the tmux backend (cmux has native detection and
status through its agent hooks; `detect.looks_blocked` still guards its
prompts):

1. **role/kind** — the existing `classify` on argv (tmux: `ps -t <tty>`;
   cmux: the hook store's agent kind, argv from `ps -t <tty>`).
2. **status** — for an agent pane: `blocked` if the last screen lines match
   a known approval/question pattern for that kind (Claude Code
   `Do you want to`, `❯ 1. Yes`, Codex `Allow`, Grok `Approve`); else `idle`
   if `window_activity` is older than `mux.tmux.idle_after` (default 30 s);
   else `working`. Marked heuristic in the roster (`agent_status_source =
   "heuristic"` is *not* added to the schema; the note goes in `tasking`).
3. **resume** — `resume.py` gains the cwd-scoped fallback column already
   present (`claude --continue`, …). `build_roster` sets `resume_argv` to
   the fallback when there is no session id **and** no other agent of the
   same kind shares the cwd on that host; otherwise `resume_argv = null`
   and `set` starts fresh with a note. On cmux the hook store's session id
   is native, and `surface resume show` says whether cmux will replay it.

## Maintenance actions, per backend

| action | herdr | tmux | cmux |
|---|---|---|---|
| `upgrade-mux` (alias `upgrade-herdr`) | as before | cold: park, kill-server, pacman/brew upgrade, new-session, set | park, AppleScript quit, `brew upgrade --cask cmux` (UNVERIFIED), `open -a cmux`, wait for the app's own resume |
| `restart-harness` | session stop/start | kill-server / new-session | quit + `open -a cmux`; the app restores workspaces and resumes agents |
| `reload-config` *(new)* | `server reload-config`, live | `source-file`, live | `reload-config`, live |
| `install-plugin` | as before | TPM `install_plugins` + `source-file`, live | refused |
| `upgrade-agents` | unchanged, backend-agnostic | unchanged | unchanged |
| `omarchy-update` | pacman hosts only | pacman hosts only (tmux on Omarchy) | refused |
| `custom` | declared | declared | declared |

"Manual step" is a first-class plan step kind (`StepKind.MANUAL`): exec
prints the instruction, waits for the operator to confirm (`--yes` does not
skip it), then continues. No planner emits one any more (the cmux relaunch
turned out to be scriptable), but the kind stays for a future backend.

## Sequence deltas (tmux)

```
set (tmux host):
  1 reach: tmux -L S display-message -p '#{version}|#{socket_path}|#{pid}'   [r]
  2 start if needed: tmux -L S new-session -d -s <first ws> -c <cwd> -P -F '#{pane_id}'
  3 (no viewport step: tmux does not need a client for send-keys)
  4 shape: new-session/new-window/split-window … -P -F '#{pane_id}' → placeholders;
           select-layout -t <s>:<w> '<layout>' when the roster has one
  5 agent: send-keys -t {pane:slot} -l '<resume argv>' ; send-keys Enter ;
           WAIT pane_current_command == <kind binary> ; WAIT quiet
  7 watcher: respawn-pane -k -t {pane:slot} -c <cwd> <argv…>   (or send-keys if the pane is a shell)
  8 prompt: WAIT quiet + no prompt pattern ; send-keys -l <text> ; Enter
secure park (tmux): send-keys -l '/exit' ; Enter ; WAIT pane_current_command != agent ; watchers: C-c
relieve reload-config (tmux): SNAP ; source-file ~/.tmux.conf ; verify display-message ; no park, no stop
```

## What this pass builds

Backends with pure argv builders and parsers (tmux parser tested against
the captured 3.7c fixture), `Capabilities`, `detect.py`, the roster/hosts
fields, planners and exec routed through the backend, the `reload-config`
action and `StepKind.MANUAL`, per-backend action behaviour, and tests.
Live execution stays behind the MVP gate for every backend.

## UNVERIFIED (per backend)

tmux: approval-prompt patterns per agent kind (heuristic by design);
`respawn-pane` on a pane whose shell is still alive (use `-k`); TPM path on
the Macs. cmux: `brew upgrade --cask cmux`; agents other than Claude (their
hooks come from `cmux hooks setup`, not yet run); split positions.
