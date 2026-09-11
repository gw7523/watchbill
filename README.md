# Watchbill

Cockpit CLI that catalogs, parks, and restores coding-agent fleets running
inside [Herdr](https://herdr.dev) 0.8.2 sessions, on one machine or many,
over SSH / Tailscale. Plugin id `sfl.watchbill`.

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
`~/.local/state/watchbill/journal.jsonl`.

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
```

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
| `upgrade-herdr` | agents | yes | new herdr binary (mise/brew/official; pacman refused, see below) |
| `restart-herdr` | agents | yes | binary already replaced |
| `omarchy-update` | agents | yes | `omarchy-update -y` on an Omarchy host |
| `upgrade-agents [--kinds claude,codex]` | matching agents | **no** | CLI bump; agents come back on the new binary with `--resume` |
| `install-plugin --plugin owner/repo [--startup-hooks]` | agents if startup hooks | if startup hooks | `herdr plugin install` |
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
src/watchbill/     package (see docs/architecture.md for the module map)
docs/              architecture, roster JSON schema, verified 0.8.2 facts, build contract
tests/             pytest; fixtures are recorded snapshot / process-info JSON
skills/watchbill/  SKILL.md for a chief-of-staff agent
plugin/            herdr-plugin.toml — thin wrapper, no logic
```

Apache-2.0. See [CONTRIBUTING.md](CONTRIBUTING.md) for the four rules.
