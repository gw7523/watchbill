# Watchbill

Cockpit CLI that catalogs, parks, and restores coding-agent fleets running
inside terminal multiplexers — [Herdr](https://herdr.dev) 0.8.2 first,
tmux 3.x, and cmux (macOS) — on one machine or many, over SSH / Tailscale.
Plugin id `agents.watchbill`. The job it does is the same on every mux: know
what is running where, stand it down cleanly, bring it back with each
agent's conversation, and wrap plugin installs, upgrades and harness
restarts around that cycle.

> **Status: architecture + skeleton.** Planners, schema, tests, and dry-run
> plans are real. Live transports (`local`, `ssh_cli`) raise
> "not implemented in this pass" until the MVP lands. See
> [docs/architecture.md](docs/architecture.md) and
> [docs/kickoff-prompt.md](docs/kickoff-prompt.md) (the build contract).

## Mental model in ten lines

1. A Herdr **session** is a server that owns PTYs. Stopping it SIGHUPs every child; there is no PTY hand-back on packaged installs.
2. Watchbill never fights that. It **catalogs** what is running (`roll`), **stands it down** cleanly (`secure`), and **brings it back** in the same shape with each agent's native `--resume` (`set`).
3. The catalog is a **roster** (JSON, schema 1). Every occupant has a durable `slot_id` (ULID) and a `human_id` (`host/session/workspace/tab/pane`). Live pane ids like `w3:p2` are hints for one server generation, never keys.
4. Occupants get a **role**: `bridge | agent | watcher | poller | server | editor | shell`. Bridges (nested herdr) are never relaunched.
5. Agents come back through `herdr agent start … -- claude --resume <id>` and then get a **resume prompt** (pinned > role template > screen excerpt).
6. Watchers come back only if their command line is in `allowlist.txt`.
7. `relieve` wraps a **maintenance window** around that cycle: secure → action → set. Actions (`upgrade-herdr`, `upgrade-agents`, `install-plugin`, `restart-harness`, `omarchy-update`, `custom`) declare their **blast radius**; `upgrade-agents` does not stop the session, `upgrade-herdr` does.
8. Every mutating verb is a **dry-run** until `--yes`. Working agents are refused without `--force`. Blocked agents are never typed into. The cockpit host is skipped without `--include-local`.
9. `current.json` points at the last **good** roster. An **occupant guard** refuses to retarget it when the count drops by half or below two (reboot can write an empty `session.json`, #3415).
10. Transport is SSH + remote `herdr --session S`, not `herdr --remote`; see the warning below.
11. **Mux is a per-host choice** (`mux = "herdr" | "tmux" | "cmux"` in `hosts.toml`). Herdr knows agents natively; tmux gets role from argv, status from quiet-time + approval-prompt patterns, and resume from each CLI's cwd-scoped `--continue` (refused when ambiguous); cmux is docs-verified only and fails closed on anything undocumented. See [docs/mux-backends.md](docs/mux-backends.md).

## Install

```bash
git clone https://github.com/gw7523/watchbill.git ~/Work/watchbill
cd ~/Work/watchbill
uv sync --group dev          # Python 3.11+, stdlib only at runtime
uv run watchbill --help
uv run pytest                # no live Herdr needed
# optional: uv tool install --editable .   → `watchbill` on PATH
```

Config lives in `~/.config/watchbill/` (`hosts.toml`, `allowlist.txt`,
`pins.toml`, `prompts/<role>.txt`, `config.toml`), rosters in
`~/.local/share/watchbill/rosters/<fleet>/`, the journal in
`~/.local/state/watchbill/journal.jsonl`. Set `WATCHBILL_HOME=/some/dir` to
keep a run's config, rosters and journal under one directory (a rehearsal, a
test). Do not isolate with `XDG_CONFIG_HOME`: herdr keeps its session sockets
there too.

**Each agent comes back as the same agent.** The roster records how every
agent was launched: its flags, the permission mode and model they imply, its
config directory, the non-secret environment that defines its seat, and a
fingerprint of its settings, plugins, hooks, MCP servers, folder trust, CLI
version and Herdr integration. On resume the flags, working directory and
seat environment are restored exactly; the on-disk parts are checked before
the agent starts, changes are reported, and a change that would break the
resume stops that host. Secret values are never recorded.

Minimal `hosts.toml`:

```toml
[fleet]
name = "home"

[[host]]
name = "rig2"
cockpit = true
transport = "local"

[[host]]
name = "ser6"
target = "ser6.tail1234.ts.net"
sessions = ["default"]
start = "systemctl --user start herdr.service"

[[host]]
name = "mac"
target = "mac.tail1234.ts.net"
mux = "tmux"                 # server socket names go in sessions (default: ["default"])

[[host]]
name = "air"
target = "air.tail1234.ts.net"
mux = "cmux"                 # macOS app; docs-verified verbs, MANUAL relaunch steps
```

## Deploying in other environments

Watchbill is one Python package with no machine-specific paths, so the same
install works on a plain host, inside a container seat, or on a Mac. Three
`hosts.toml` keys make that practical:

| Key | What it does | Example |
|---|---|---|
| `exec_prefix` | runs every command for that host *inside an environment* on the target, locally or over ssh | `["distrobox", "enter", "sfl", "--"]` |
| `exclude` | globs against an occupant's command line or `human_id`; a match is catalogued but never parked, relaunched or restored, and a window that would stop or close what it runs in is refused | `["*distrobox enter sfl*"]` |
| `sessions` | which multiplexer sessions (herdr) or server sockets (tmux) belong to this host | `["default"]` |

A Watchbill installed inside one machine's seat can manage that seat as its
cockpit and reach the matching seat on another machine:

```toml
[[host]]
name = "sfl-rig2"          # the seat Watchbill runs in
cockpit = true
transport = "local"

[[host]]
name = "sfl-ser6"          # the same kind of seat on another machine
target = "ser6-lan"
exec_prefix = ["distrobox", "enter", "sfl", "--"]
```

Where Watchbill cannot rely on the target looking like the machine it was
written on, it asks the target: the herdr `session.json` safety copy is found
from herdr's own reported socket path, the headless start uses `setsid` where
it exists and a `nohup` background start where it does not (macOS), and the
agent environment probe reads `/proc`, so on a non-Linux target the roster
says the environment was not captured instead of claiming it. The SSH
identity an environment uses to reach another machine is that environment's
own concern; Watchbill only uses the `target` it is given.

## Command cheat sheet

| Command | Does | Mutates? |
|---|---|---|
| `watchbill roll [--explain]` | catalog every occupant on every host | no |
| `watchbill snap [-m heartbeat]` | roll + write a versioned roster; retarget `current.json` if the guard passes | roster only |
| `watchbill secure park [targets]` | `/exit` agents, interrupt watchers, keep panes | with `--yes` |
| `watchbill secure fold\|dismiss\|host …` | park then close workspace / stop session / every session | with `--yes` |
| `watchbill secure detach` | snap only | no |
| `watchbill set [targets] [--no-prompt]` | start session, attach viewport, rebuild shape by labels, resume agents, relaunch allowlisted watchers, prompt | with `--yes` |
| `watchbill relieve <action> [--mode cold\|live] [--host H] [--resume]` | secure → action → set, one host at a time | with `--yes` |
| `watchbill status` | live roll vs last roster | no |
| `watchbill doctor` | version, protocol, install flavor, handoff support | no |

Targets are `slot_id`, full `human_id`, or a label suffix
(`personal-config/1/p1`, or just `personal-config`). A label that matches on
more than one host needs `--host` or the plan is refused (exit 3).

Exit codes: `0` ok · `1` partial (a host was down) · `2` usage · `3` refused
unsafe plan · `4` transport/action error.

Actions for `relieve`:

| action | parks | session stop | use |
|---|---|---|---|
| `upgrade-mux` (alias `upgrade-herdr`) | agents | yes | new herdr/tmux/cmux binary (mise/brew/official; pacman refused, see below; cmux needs a manual relaunch) |
| `reload-config` | nothing | **no** | live config re-read: `herdr server reload-config`, `tmux source-file`; refused on cmux |
| `restart-herdr` | agents | yes | binary already replaced |
| `omarchy-update` | agents | yes | `omarchy-update -y` on an Omarchy host |
| `upgrade-agents [--kinds claude,codex]` | matching agents | **no** | CLI bump; agents come back on the new binary with `--resume` |
| `install-plugin --plugin owner/repo [--startup-hooks]` | agents if startup hooks | if startup hooks | `herdr plugin install`; tmux: TPM + `source-file` (live); refused on cmux |
| `restart-harness` | agents | yes | plugin / integration / config bounce |
| `custom --cmd … [--park roles] [--session-stop]` | declared | declared | anything else |

## Herdr 0.8.2 warning: `--remote` version skew

Omarchy's `herdr 0.8.2-1` package has reported `0.8.0` in some paths while
speaking protocol 20, and `herdr --remote` refuses on a version-string
mismatch. Watchbill therefore defaults to

```
ssh -o BatchMode=yes -o ConnectTimeout=5 <target> -- herdr --session <S> …
```

so the only version that has to match is the remote CLI against its own
server. `herdr_remote` is an opt-in transport that `doctor` unlocks only when
versions agree.

Two more 0.8.2 facts that shape the design: `herdr status server --json`
advertises `live_handoff: true` even on a pacman install, so `relieve --mode
live` trusts install flavor, not the flag, and errors on packaged hosts; and
native agent resume needs a client viewport (#2064), so `set` attaches one
before any `agent start`.

## Layout

```
AGENTS.md          agent bootstrap: read order, status, work loop, rules (CLAUDE.md points here)
src/watchbill/     package (see docs/architecture.md for the module map)
docs/              architecture, mux-backends design, roster JSON schema, verified herdr/tmux/cmux facts, build contract
tests/             pytest; fixtures are recorded snapshot / process-info JSON
skills/watchbill/  SKILL.md for a chief-of-staff agent
plugin/            herdr-plugin.toml — thin wrapper, no logic
```

Apache-2.0. See [CONTRIBUTING.md](CONTRIBUTING.md) for the four rules.
