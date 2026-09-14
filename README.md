# Watchbill

**Keep a fleet of long-running coding agents alive across restarts, upgrades
and machines.**

Watchbill is a cockpit CLI for people who run several coding agents — Claude
Code, Grok, Codex, OpenCode — as persistent sessions inside a terminal
multiplexer ([Herdr](https://herdr.dev), tmux, or [cmux](https://cmux.com) on
macOS), on one machine or across a few reached over SSH or Tailscale. It
catalogs what is running where, stands it down cleanly, brings every agent
back into the same pane with its own conversation and configuration, and
wraps multiplexer upgrades, agent CLI upgrades, plugin installs and harness
restarts around that cycle. Every mutating command is a dry-run until you
say `--yes`.

## The problem

A multiplexer server owns the PTYs of every agent inside it. The moment you
upgrade it, restart it, install a plugin with startup hooks, or reboot the
box, every agent dies with it. With one agent that is a nuisance; with eight
agents across three machines it is an afternoon:

- you have to remember which pane held which conversation, on which host, in
  which directory, started with which flags and permission mode;
- each agent CLI resumes differently (`claude --resume <id>`,
  `codex resume <id>`, `opencode --session <id>`, `grok --resume <id>`), and
  only if you still have the session id;
- a server restarted from an SSH shell loses the desktop session, so resumed
  agents can no longer open a browser or reach the keychain;
- one agent mid-task, or sitting on an approval dialog, must not be typed
  into; one you forgot about gets killed;
- plugins, hooks, MCP servers and folder trust drift between what the agent
  had and what it comes back with;
- and it all happens again next week.

Multiplexers each solve a slice of this (Herdr knows its agents, cmux
restores its own, tmux knows nothing), none of them across machines, and
none of them around an upgrade of themselves.

## What Watchbill does

| Verb | Does | Mutates? |
|---|---|---|
| `watchbill roll` | catalog every occupant of every session on every host: role, agent kind, status, session id, cwd, flags | no |
| `watchbill snap` | roll and write a versioned **roster** (JSON); `current.json` points at the last good one | roster only |
| `watchbill secure park\|fold\|dismiss\|host [targets]` | `/exit` the agents (only the idle ones), interrupt watchers; optionally close workspaces or stop sessions | with `--yes` |
| `watchbill set [targets]` | start the session if needed, rebuild workspaces and panes by label, resume each agent on its session id with its recorded flags, relaunch allowlisted watchers, send each agent a resume prompt | with `--yes` |
| `watchbill relieve <action>` | a maintenance window: secure → action → set, one host at a time, resumable after a failed host | with `--yes` |
| `watchbill status` | live roll against the last roster: what appeared, vanished, changed | no |
| `watchbill doctor` | per host: mux version, install flavor, whether a live handoff is possible, agent CLI versions, mux-specific warnings | no |

The roster is the contract. Each occupant has a durable `slot_id` and a
`human_id` of the form `host/session/workspace/tab/pane`; live pane ids are
hints for one server generation, never keys. Rosters never contain secrets:
credential flags are redacted, and environment variables are recorded by
name only when they look like secrets.

**Each agent comes back as the same agent.** At park time Watchbill records
the agent's flags, the permission mode and model they imply, its config
directory, its non-secret seat environment, and a fingerprint of its
settings, plugins, hooks, MCP servers, folder trust and CLI version. At
resume the flags, working directory and environment are restored exactly,
the on-disk configuration is verified before the agent starts, and a change
that would break the resume stops that host instead of guessing.

**The session comes back in the same environment.** A server restarted over
SSH is given the user's live desktop session variables (display, bus,
desktop), never the SSH connection Watchbill arrived on; on macOS a Herdr or
tmux server is started through launchd or the app so the keychain works.

## What is supported

| Multiplexer | Verified on | Agent detection | Agent status | Resume | Session stop / start |
|---|---|---|---|---|---|
| **Herdr** 0.8.2, 0.9.0 | Linux (Omarchy), macOS | native | native | native session id → each CLI's resume flag | `session stop` / the host's `start` (systemd unit, launchd, or detached server) |
| **tmux** 3.x | Linux, macOS | from the process table (`ps -t`) | heuristic: quiet time + approval-prompt patterns | cwd-scoped `--continue`, refused when two agents of one kind share a directory | `kill-server` / `new-session -d` with the exact layout re-applied |
| **cmux** 0.64 | macOS | native (cmux agent hooks) | native (hook lifecycle) | native session id; the app resumes its own agents on relaunch and Watchbill waits for it | AppleScript quit / `open -a cmux` |

| Agent CLI | Park | Resume form | Live-tested |
|---|---|---|---|
| Claude Code | `/exit` | `claude <flags> --resume <id>` | Herdr, tmux, cmux |
| Grok | `/exit` | `grok <flags> --resume <id>` | Herdr, tmux |
| Codex | `/quit` | `codex resume <flags> <id>` | Herdr |
| OpenCode | `/exit` | `opencode <flags> --session <id>` | Herdr |
| Gemini CLI | `/exit` | `gemini <flags> --resume <id>` | flag table only |
| Cursor | `/exit` | `cursor-agent <flags> --resume <id>` | flag table only |

Transports: `local`, `ssh_cli` (per-host `ssh_options`), and an
`exec_prefix` so the same Watchbill can manage a seat inside a container
(for example `distrobox enter <name> --`) on this or another machine.
Watchbill itself runs on Linux or macOS with Python 3.11+ and no runtime
dependencies; the hosts need their mux CLI and `ssh` access with keys.

## Install

```bash
uv tool install git+https://github.com/gw7523/watchbill      # or: pipx install git+…
watchbill --help

# from a clone
git clone https://github.com/gw7523/watchbill && cd watchbill
uv sync --group dev && uv run pytest      # no live mux needed for the tests
```

Configuration lives in `~/.config/watchbill/`: `hosts.toml` (required),
`allowlist.txt` (watcher command lines allowed to relaunch), `pins.toml`
(resume prompts per agent), `prompts/<role>.txt`, `config.toml`. Rosters go
to `~/.local/share/watchbill/rosters/<fleet>/`, the journal to
`~/.local/state/watchbill/journal.jsonl`. `WATCHBILL_HOME=/some/dir` keeps
all of it under one directory for a rehearsal.

## Quick start

`hosts.toml` for a cockpit and three targets:

```toml
[fleet]
name = "home"

[[host]]                     # the machine you run watchbill on
name = "rig2"
cockpit = true
transport = "local"

[[host]]                     # Herdr on Linux, server owned by a systemd user unit
name = "ser6"
target = "ser6.tail1234.ts.net"
sessions = ["default"]
start = "systemctl --user start herdr.service"
ignore = ["*distrobox enter sfl*"]      # catalogued, never touched; a stop may end it

[[host]]                     # tmux on a Mac
name = "mini-tmux"
target = "mini.tail1234.ts.net"
ssh_options = ["-i", "~/.ssh/id_ed25519_personal"]
mux = "tmux"
sessions = ["work"]          # tmux server socket names (-L)
mux_options = { idle_after_s = 30 }

[[host]]                     # cmux on the same Mac
name = "mini-cmux"
target = "mini.tail1234.ts.net"
ssh_options = ["-i", "~/.ssh/id_ed25519_personal"]
mux = "cmux"
sessions = ["app"]
```

Then:

```bash
watchbill doctor                       # every host reachable? versions, install flavor, warnings
watchbill roll                         # who is running where
watchbill snap -m manual               # write the first roster
watchbill secure park                  # dry-run: what would be parked, what is refused and why
watchbill secure park --yes            # park every idle agent on every target host
watchbill set --yes                    # bring them all back, each on its own conversation
                                       # (falls in from the roster taken before the park)
```

Targets for `secure` and `set` are a `slot_id`, a full `human_id`, or a label
suffix (`personal-config/1/p1`, or just `personal-config`); `--host` breaks
ties. A `working` agent is refused unless `--force`; an agent on an approval
dialog is never typed into. The cockpit host is left alone unless
`--include-local`.

Put a resume prompt in `pins.toml` for agents that should be told what to do
after they come back:

```toml
[pins."ser6/default/personal-config/1/p1"]
prompt = "You were relaunched by Watchbill. Re-read TODO.md and continue the open item."
```

## Maintenance windows

`relieve` runs one action per host, rolling: park the affected agents, stop
the session if the action needs it, run the action, bring the session and
the agents back, verify, journal. A failed host stops the run; `--resume`
continues with the hosts not yet done. Each action declares its blast
radius:

| action | parks | stops the session | what it runs |
|---|---|---|---|
| `upgrade-mux` (alias `upgrade-herdr`) | agents | yes | Herdr: the installer for its flavor (mise, official; pacman refused unless `--allow-partial-pacman`). tmux: `brew upgrade tmux` / `pacman -S tmux`. cmux: `brew upgrade --cask cmux` (not yet verified live) |
| `restart-harness` / `restart-herdr` | agents | yes | nothing but the stop and start: for a binary already replaced, a config that needs a bounce, a plugin with startup hooks |
| `reload-config` | nothing | no | `herdr server reload-config`, `tmux source-file`, `cmux reload-config` |
| `upgrade-agents [--kinds claude,codex]` | those agents only | **no** | `mise upgrade` / `npm install -g` / `brew upgrade` / the CLI's self-update, per install flavor; Herdr integration hooks refreshed |
| `install-plugin --plugin owner/repo [--ref v1] [--startup-hooks]` | agents if startup hooks | if startup hooks | `herdr plugin install`; tmux: TPM + `source-file`; cmux: refused (no plugin system) |
| `omarchy-update` | agents | yes | `omarchy-update -y` on an Omarchy host |
| `custom --cmd '…' [--park agent,watcher] [--session-stop] [--may-reboot]` | as declared | as declared | anything else |

### Example: upgrade Herdr on every Linux box

```bash
watchbill doctor                                   # flavors: rig2 mise, ser6 official, vps mise
watchbill relieve upgrade-mux                      # dry-run: the full plan, host by host
watchbill relieve upgrade-mux --yes --expected-version 0.9.0
```

Per host: `/exit` each idle agent and wait for it to leave, copy the session
file aside, `herdr session stop`, run the installer, start the server with
the user's desktop environment (`start =` in hosts.toml, or a detached
`herdr --session S server`), rebuild any workspace the server did not
restore, `herdr agent start … -- claude --resume <id> <flags>` for each
agent, wait until each one is idle, send its pinned prompt, and check that
`doctor` now reports 0.9.0. A host whose agents are `working` refuses the
window; park them first, wait, or pass `--force` knowingly. An Omarchy host
with a pacman-owned Herdr is refused: use `omarchy-update` there, or
`--allow-partial-pacman` if you accept a partial upgrade.

### Example: upgrade tmux on a Mac

```bash
watchbill relieve upgrade-mux --host mini-tmux --yes
```

tmux keeps nothing across `kill-server`, so Watchbill records the exact
window layouts and every pane's command first, parks the agents, kills the
server, runs `brew upgrade tmux`, creates the sessions and windows again with
`new-session -d` / `new-window`, re-applies each recorded layout, relaunches
the allowlisted watchers, and resumes the agents with their cwd-scoped
`--continue` form (when two agents of one kind share a directory it refuses
to guess and starts that one fresh, with a note). Start the first tmux
server from a GUI terminal on the Mac, not over SSH, if the agents need the
keychain.

### Example: cmux after an app update

```bash
watchbill relieve restart-harness --host mini-cmux --yes     # or upgrade-mux
```

cmux's own agent hooks tell Watchbill which surface holds which session and
whether it is idle. The window parks the agents, quits the app through
AppleScript (`app.confirmQuit = "never"` in `~/.config/cmux/cmux.json`,
which `doctor` checks), relaunches it with `open -a cmux`, and then *waits*:
cmux rebuilds its workspaces with the same ids and resumes each agent itself.
Only if an agent does not reappear does Watchbill type the recorded resume
command. The pinned prompt is sent once the agent is idle.

### Example: the whole fleet, then the agents

```bash
watchbill relieve restart-harness --yes            # every target host, one at a time
watchbill relieve restart-harness --yes --resume   # after fixing a host that failed
watchbill relieve upgrade-agents --kinds claude --yes   # Claude CLIs only; sessions stay up
watchbill relieve install-plugin --plugin acme/herdr-notify --startup-hooks --yes
watchbill relieve reload-config --yes              # live, nothing parked
```

`upgrade-agents` parks only the agents of the kinds being upgraded, leaves
the session and every other pane running, upgrades each CLI the way it was
installed, and resumes those agents on the new binary with their original
session ids and flags.

### Keeping the roster fresh

```bash
watchbill snap -m heartbeat      # from cron or a timer
watchbill status                 # what changed since the last roster
```

`current.json` is only retargeted when the occupant count is plausible (a
reboot can leave a mux with an empty session file); `snap --force` overrides
the guard.

## Safety model

- Dry-run by default; `--yes` executes. The plan you see is the plan that runs.
- Only `exec.py` mutates anything. Planners are pure functions of the roster
  and collected facts; transports are the only code that knows about SSH.
- Never an unplanned server stop: `herdr server stop`, a `tmux kill-server`
  outside a declared window, or a cmux quit outside one needs
  `--force-server-stop`.
- Never a keystroke into an agent that is working or on an approval dialog;
  a Claude with Remote Control connected is refused (it ignores `/exit`)
  unless the host opts into disconnecting it.
- `exclude` globs protect occupants: never parked, never restored, and a
  window that would stop their session is refused. `ignore` globs mark
  occupants as out of scope without protecting them.
- Exit codes: `0` ok · `1` partial (a host was down) · `2` usage · `3` refused
  unsafe plan · `4` transport or action error. A journal records every step.

## hosts.toml reference

| key | default | meaning |
|---|---|---|
| `name` | — | host name used in `human_id` and `--host` |
| `target` | — | ssh destination (`user@host` or an ssh config alias); omit with `transport = "local"` |
| `transport` | `ssh_cli` | `local` or `ssh_cli` |
| `cockpit` | `false` | the host Watchbill runs on; skipped by mutating verbs unless `--include-local` |
| `mux` | `herdr` | `herdr`, `tmux`, `cmux` |
| `sessions` | `["default"]` | Herdr session names, tmux socket names, or a label for the cmux app |
| `start` | per mux | how to start a session: a systemd unit, `launchctl kickstart gui/$(id -u)/<label>` on macOS, or the built-in detached server start; `open -a cmux` for cmux |
| `ssh_options` | `[]` | extra ssh arguments, e.g. `["-i", "~/.ssh/id_x"]` |
| `exec_prefix` | `[]` | run every command inside an environment on the target, e.g. `["distrobox", "enter", "sfl", "--"]` |
| `exclude` / `ignore` | `[]` | globs against an occupant's command line or `human_id` |
| `mux_options` | `{}` | tmux: `idle_after_s`, `conf`; cmux: `socket`, `idle_after_s`; herdr: `attach_before_resume`, `disconnect_remote_control` |

## Known limits

- No live handoff of PTYs: a session stop always ends its agents; Watchbill
  resumes conversations, it cannot keep a process alive across the stop.
- tmux agent status is a heuristic; an `unknown` status needs `--force` to
  park, like a `working` one.
- Gemini CLI and Cursor resume forms come from their `--help`, not from a
  live run. `brew upgrade --cask cmux` has not been run live.
- A freshly relaunched cmux agent reads `unknown` until its first turn.
- Herdr `--remote` is not used as a transport (version-string skew in 0.8.2
  packages); every host is reached by `ssh … -- herdr --session S`.

## Documentation

```
docs/architecture.md      module map, sequences, pitfalls → tests
docs/mux-backends.md      the mux axis, capability matrix, per-mux action table
docs/herdr-0.8.2-facts.md, docs/tmux-3.7-facts.md, docs/cmux-facts.md, docs/macos-facts.md,
docs/agent-cli-facts.md   what was verified on live boxes; check before touching any argv
docs/roster.schema.json   roster schema 1
examples/tmux/            runnable walkthrough: a throwaway tmux server cycled through a window
skills/watchbill/         SKILL.md for an agent that drives Watchbill
plugin/                   herdr-plugin.toml (id agents.watchbill)
AGENTS.md                 how to work on this repo (CLAUDE.md points here)
```

Apache-2.0. See [CHANGELOG.md](CHANGELOG.md) and
[CONTRIBUTING.md](CONTRIBUTING.md).
