# Watchbill — architecture kickoff

Paste this into a **new Claude session** as the builder. Paste the **Reviewer brief** into a **Grok** session (or the same session as a second agent) after Claude produces architecture artifacts. Drop `watchbill-spec.md` in the repo root; it is the product spec. This prompt is the build contract.

You will open a **new personal GitHub repo**. Suggested name: `watchbill`. License: Apache-2.0. Do not invent an org. Do not publish under `sfl` unless the owner says so. Plugin id is `agents.watchbill` (not SFL-specific); the git remote is personal.

---

## Builder brief (Claude)

### Role

You are the architect and first implementer of **Watchbill**: a cockpit CLI that catalogs, parks, and restores coding-agent fleets running inside [Herdr](https://herdr.dev) 0.8.2 sessions on one machine or many, over SSH/Tailscale.

A second agent (Grok) will review your architecture before you write substantial implementation. Produce reviewable artifacts first. Do not skip to a 4,000-line CLI.

### Product in one paragraph

Watchbill is the missing control plane for a Herdr fleet. It answers: which agents and watchers are running, in which directory, session, pane slot, and machine; how to stand them down without losing the map; how to bring them back in the same layout and cwd, resuming native agent conversations and allowlisted watchers; and how to wrap that park/restore cycle around a **maintenance window** (upgrade Herdr, upgrade agent CLIs, install plugins, restart the harness).

It is not a multiplexer, not an agent runtime, and not Herdr Cloud.

### Why this tool exists

Herdr 0.8.2 already has `session.snapshot`, `layout.export`/`apply`, `pane.process_info`, `agent list`/`prompt`/`start`, `session stop`, and `herdr --remote`. None of those join into a fleet roster with durable identity, tasking classification, stored resume prompts, watcher relaunch, or a safe upgrade window.

0.9 multi-machine TUI does not retire this. Its agent CLI stays server-scoped.

**Hard constraint — memorize this:** Herdr owns the PTY master. There is no public export-and-re-adopt-PTY API. Stopping a server without live handoff SIGHUPs every child. Live handoff (`herdr update --handoff`) is experimental and **only** works for official `curl | sh` installs. Omarchy/pacman, mise, Homebrew, and Nix cannot hand off. This user’s machines are Omarchy-packaged. Cold drain-and-resume is the default path. Do not design as if live PTY adoption were available.

### Source of truth

1. `watchbill-spec.md` in this repo (full product spec).
2. This prompt, when they conflict on *process* (how to build). Spec wins on *behavior*.
3. Live `herdr --help` / `herdr api schema --json` on an 0.8.2 host when a CLI flag is uncertain. Do not invent Herdr flags.

Target runtime: **Herdr 0.8.2, socket protocol 20**. Forward-compatible with 0.9 `machine` profiles as a host-discovery adapter only.

### Goals

- G1. Roster every occupant of every known Herdr server: host, session, workspace, tab, pane, cwd, role, tasking.
- G2. Secure (stand down) selected occupants, a workspace, a session, a host, or the fleet.
- G3. Set (fall in) from a roster: sessions up, shape restored, agents resumed via native session refs, watchers relaunched if allowlisted, then `herdr agent prompt` to continue the workflow.
- G4. Work on 0.8.2 with SSH/Tailscale. No `herdr machine` dependency.
- G5. Survive 0.9 without a schema break.
- G6. Never use `w1:p2` as a durable key.
- G7. Drivable by a chief-of-staff agent via a skill file.
- G8. Relieve: a generic maintenance-window runner. Not a verb per operation.

### Non-goals (v1)

- Cross-machine agent-to-agent collaboration.
- Replacing Herdr / tmux / k8s.
- Pixel-perfect scrollback as a product feature.
- Windows as a Herdr server (Windows cockpit talking to Linux workers is fine).
- Auto-hibernate / RAM reclaim.
- Hosted control plane.
- Depending on `herdr-resurrect`, `herdr-hub`, `herdr-suspend-workspace`, or `herdr-muster` (the last is a *different* plugin — project switcher, id `kichel.muster`).
- Inventing a live-PTY-import feature Herdr does not have.

### Name and verbs (locked)

| Piece | Value |
|---|---|
| Product | Watchbill |
| CLI | `watchbill` |
| Plugin id | `agents.watchbill` |
| Skill | `skills/watchbill/SKILL.md` |

Do not rename to Muster, Fleet, Corral, Fold, or Roundup.

| Verb | Meaning |
|---|---|
| `roll` | catalog |
| `snap` | write versioned roster |
| `secure` | stand-down |
| `set` | restore + relaunch + stdio resume |
| `relieve` | secure → action → set (alias: `overhaul`) |
| `status` | live vs last roster |

### Stack (locked unless you write a one-page dissent Grok accepts)

- Language: **Python 3.11+**. stdlib first (`tomllib`, `json`, `argparse` or `typer`, `subprocess`, `pathlib`). Optional: `pydantic` v2 for the roster schema only.
- Packaging: `uv` + `pyproject.toml`. Console script `watchbill`.
- No daemon. Heartbeat is a systemd user timer calling `watchbill snap -m heartbeat`.
- Tests: `pytest`. Fixtures are recorded `api snapshot` + `process_info` JSON. No live Herdr required for unit tests.
- Plugin: thin wrapper that execs the CLI. Logic does not live in the plugin.
- Do not start from Rust/Go/TS for MVP. A later rewrite is allowed; the roster JSON schema is the stability boundary.

### Architecture you must design

Produce these artifacts **before** filling in command bodies:

1. `docs/architecture.md` — module map, data flow, sequence diagrams (text) for `roll`, `secure park`, `set`, `relieve cold`, `relieve live-rejected`.
2. `docs/roster.schema.json` — JSON Schema draft 2020-12 for roster schema 1.
3. `src/watchbill/` package skeleton with empty-or-stub modules and docstrings that state contracts.
4. `tests/` with the acceptance tests from the spec, several of them already failing-red against stubs (so we know they are real).
5. `README.md` — install, 10-line mental model, command cheat sheet, 0.8.2 warning about `--remote` version skew.
6. `skills/watchbill/SKILL.md` — how a chief-of-staff agent is allowed to drive this tool.
7. `plugin/herdr-plugin.toml` — actions that call the CLI.

#### Module boundaries (required)

```
src/watchbill/
  cli.py              # parse, dispatch, exit codes. no herdr calls.
  hosts.py            # hosts.toml
  transport/
    base.py           # HostSession client protocol
    local.py
    ssh_cli.py        # DEFAULT. ssh BatchMode + remote `herdr --session S`
    ssh_socket.py     # later
    herdr_remote.py   # later; only after doctor says versions match
  collect.py          # snapshot + process_info + layout.export
  classify.py         # role from argv + agent list
  roster.py           # load/save/merge/guard
  slots.py            # durable slot_id assignment
  plan_secure.py      # pure: roster+live → Plan
  plan_set.py         # pure
  plan_relieve.py     # pure: compose secure + action + set
  actions/            # maintenance-window plugins
    base.py           # Action protocol
    upgrade_herdr.py
    restart_herdr.py
    omarchy_update.py
    upgrade_agents.py
    install_plugin.py
    restart_harness.py
    custom.py
  exec.py             # run plans, journal, timeouts, --yes/--dry-run
  doctor.py
  prompts.py
  journal.py
```

Rules:

- Collectors and planners are pure-ish. `exec.py` is the only place that mutates a live Herdr server.
- Transport is the only place that knows SSH vs local vs `--remote`.
- Actions declare blast radius. They do not call `session stop` themselves. They return a descriptor; `plan_relieve` + `exec` apply it.

```python
class Action(Protocol):
    name: str
    def blast_radius(self, ctx) -> BlastRadius:
        """park_roles, needs_session_stop, needs_client_attach, allow_reboot"""
    def probe(self, host) -> Probe:
        """install flavor, current versions, handoff supported?"""
    def commands(self, host, probe) -> list[RemoteCmd]:
        """exact argv that would run; dry-run prints these"""
    def verify(self, host) -> Verify:
        """post-action doctor checks, expected version"""
```

This is the extension point for “upgrade Claude CLI”, “install plugin X”, “restart harness after plugin install”. Do not add `watchbill upgrade-claude` as a top-level verb.

#### Durable identity

```
slot_id     ULID, assigned once, stored in ~/.local/share/watchbill/slots.json
human_id    host/session/workspace_label/tab_label/pane_label
live_ids    w3:p2 etc. — hints for this process generation only
```

Plans key on `slot_id` / `human_id`. After `set`, rewrite `live_ids`.

Roles (first match): `bridge | agent | watcher | poller | server | editor | shell`.

`bridge` = argv contains `herdr --remote` or `herdr --session`. Never relaunch.

#### Transports (0.8.2)

Default: `ssh -o BatchMode=yes -o ConnectTimeout=5 $target -- herdr --session $S ...`

Do not default to `herdr --remote`. Omarchy’s 0.8.2 package has reported `herdr 0.8.0` while speaking protocol 20, which breaks remote version matching.

Socket order (Herdr’s): `--session` → `HERDR_SOCKET_PATH` → `HERDR_SESSION` → `~/.config/herdr/herdr.sock`. Named: `~/.config/herdr/sessions/<name>/herdr.sock`.

Public remote commands Watchbill may run — and nothing else:

```
herdr --session S status server|client
herdr --session S agent list|start|prompt|send-keys|wait
herdr --session S pane list|process-info|run|send-keys|close|read
herdr --session S workspace list|create|close
herdr --session S session list|stop
herdr --session S api snapshot
herdr --session S plugin list|install|status
# layout.export / layout.apply via documented CLI or socket
```

Never `herdr server stop` unless `--force-server-stop`. Default session: `herdr session stop default`.

#### Secure modes

`detach` (snap only) | `park` (`/exit` or SIGINT, keep pane) | `fold` (close workspace) | `dismiss` (session stop) | `host` (every session).

Dry-run unless `--yes`. Refuse `working` agents unless `--force`. Skip the pane Watchbill itself is running in (`HERDR_PANE_ID`). Do not send `/exit` into a `blocked` approval dialog.

#### Set

1. Reach host.
2. Start session via `hosts.toml` `start` capability.
3. **Attach a client.** 0.8.x will not spawn `claude --resume` headless (#2064).
4. Reconcile shape by labels, not pane ids. `layout.apply` with `command` stripped; Watchbill launches commands.
5. Agents with `agent_session` → `herdr agent start … -- <resume argv>`.
6. Unref agents → start fresh, then prompt.
7. Allowlisted watchers → `pane run`.
8. `agent prompt` with pinned / role template / excerpt-drafted text.
9. Rewrite live_ids. Write post-set roster. Occupant-count guard on `current.json`.

Native resume argv (0.8.2):

| kind | argv |
|---|---|
| claude | `claude --resume <id>` |
| grok | `grok --resume <id>` |
| codex | `codex resume <id>` |
| cursor | `cursor-agent --resume <id>` |
| opencode | `opencode --session <id>` |
| hermes | `hermes --resume <id>` |

#### Relieve — maintenance window, not “the herdr upgrader”

Cold (default, Omarchy):

1. snap (`pre-relieve`), occupant guard
2. park declared roles
3. copy `session.json` aside (#3415 persist.clear race)
4. session stop **only if** the action’s blast radius says so
5. run action commands
6. doctor + optional `--expected-version`
7. start + attach if session was stopped
8. set the affected slots
9. journal; next host (`--rolling` default)
10. `--resume` skips hosts marked `set-complete`

Live (`--mode live`): only if doctor says `handoff: supported`. Otherwise **error**, do not silently become cold.

Built-in actions and blast radius:

| Action | Park | Session stop | Typical use |
|---|---|---|---|
| `upgrade-herdr` | agents | yes | new herdr server binary |
| `restart-herdr` | agents | yes | binary already replaced |
| `omarchy-update` | agents | yes | Update > Omarchy equivalent |
| `upgrade-agents` | matching agents | **no** | `claude`/`grok`/`codex` version bump; harness stays |
| `install-plugin` | agents if startup hook | if `[[startup]]` | `herdr plugin install owner/repo` |
| `restart-harness` | agents | yes | plugin/integration/config needs bounce |
| `custom` | declared | declared | `--cmd` |

`upgrade-herdr` flavor probe: pacman/omarchy → never call `herdr update`; mise → `mise upgrade herdr`; brew → `brew upgrade herdr`; official installer → `herdr update`. Arch partial upgrades off unless `relieve.allow_partial_pacman = true`.

`upgrade-agents` probe: for each kind present in the roster, detect the CLI (`which claude`, `claude --version`, etc.), run the kind’s documented self-update if the operator passed `--yes`, then `set` those slots so they come back on the new binary with `--resume`. Session stays up. Refresh `herdr integration install` / `herdr integration status` after the CLI bump if the integration ships separately.

`install-plugin`: `herdr plugin install owner/repo [--yes]` does **not** need a running server. `[[startup]]` hooks run after session restore / live handoff, **not** on plugin link/enable. So “install a plugin that needs a harness restart” is `install-plugin` followed by `restart-harness` when the manifest has startup hooks. Do not assume enable = hook fired.

Resume argv table must keep a `verify` column. Cite these, do not invent flags. If uncertain, mark `UNVERIFIED-0.8.2` and probe `herdr agent start --help` / `<bin> --help` before MVP:

| kind | resume argv | verify |
|---|---|---|
| claude | `claude --resume <id>` | official herdr integration |
| grok | `grok --resume <id>` (fallback `--continue`) | official herdr integration |
| codex | `codex resume <id>` | official herdr integration |
| cursor | `cursor-agent --resume <id>` | official herdr integration |
| opencode | session flag varies | **probe `--help` before coding it** |
| hermes | `hermes --resume <id>` | official herdr integration |

`layout.export` / `layout.apply` are socket API methods (`herdr api` / documented JSON-RPC). `layout.apply` creates **new** pane ids. Closed pane ids are never reused. Plan `set` as “rebuild by labels, then rewrite live_ids.”

Do not reboot the cockpit. `--include-local` required to touch the machine Watchbill is running on.

#### Paths

```
~/.config/watchbill/hosts.toml
~/.config/watchbill/allowlist.txt
~/.config/watchbill/pins.toml
~/.config/watchbill/prompts/<role>.txt
~/.config/watchbill/config.toml
~/.local/share/watchbill/rosters/<fleet>/
~/.local/share/watchbill/slots.json
~/.local/state/watchbill/journal.jsonl
```

`current.json` is a symlink to the last *good* roster. Refuse to retarget it if occupant count drops by `guard.drop_ratio` (0.5) or below `guard.min_occupants` (2) unless `--force`.

#### Exit codes

0 ok · 1 partial (some hosts down) · 2 usage · 3 refused unsafe plan · 4 transport/action error

### 0.8.2 pitfalls you must encode as tests or comments

1. `--remote` version skew on Omarchy packages.
2. `pane layout --current` is wrong (#2297). Always pass an explicit id.
3. `layout.export` often omits `command`. Always walk `process_info`.
4. Prefer `foreground_cwd` over pane cwd when present.
5. Pane ids are generation-scoped.
6. Native agent resume needs a client viewport (#2064).
7. `agent_session` missing until the integration reports (Codex often `unref`).
8. Reboot persist.clear can write empty `session.json` (#3415). Occupant guard + heartbeat snap.
9. Skip self pane.
10. Nested Herdr = `bridge`.
11. Label collision across hosts requires `--host` or fail closed (exit 3).
12. `agent prompt` into a `blocked` agent is rejected by Herdr 0.8.2 (`agent_blocked`). Don’t do it.
13. Codex “Update available” dialog has been misclassified as idle (#3632). Treat unexpected agent-exit after prompt as a relieve failure, not success.

### What to build in this pass (architecture + skeleton)

Do these, in order:

1. Repo metadata: `pyproject.toml`, `README.md`, `LICENSE` (Apache-2.0), `.gitignore`, `watchbill-spec.md` (already exists — do not rewrite the spec; you may add `docs/` that cite it).
2. `docs/architecture.md` + `docs/roster.schema.json`.
3. Package skeleton with the module map above. Each planner module exports typed `Plan` / `Step` objects.
4. `watchbill --help` and `watchbill roll --help` etc. work, even if `roll` prints `not implemented` on a live host.
5. Working unit tests for: classify, durable id stability, occupant-count guard, blast-radius matrix (upgrade-agents does not request session stop; upgrade-herdr does), live-mode rejected when handoff unsupported, dry-run plan contains no mutating herdr verbs.
6. Skill file + plugin manifest stubs.
7. A `CONTRIBUTING.md` that says: planners are pure, exec mutates, transports isolate SSH, actions declare blast radius.

Stop there. Do **not** implement SSH transport, live collect, or exec against a real Herdr in this pass unless the owner says “implement MVP next.”

### Working agreements

- No secrets in rosters by default. Screen excerpts are cockpit-local (`snap.excerpt_sync = false`).
- Dry-run is the default for every mutating command.
- Prefer small commits: schema, planners, cli stubs, tests.
- If you must guess a Herdr 0.8.2 flag, mark `UNVERIFIED-0.8.2` in the architecture doc and keep a fallback path.
- Do not vendor other herdr plugins.

### Deliverable checklist (your PR/commit message should list these)

- [ ] architecture.md with sequence diagrams for roll / secure park / set / relieve cold / upgrade-agents (no session stop)
- [ ] roster.schema.json validates a sample roster
- [ ] Action protocol + blast-radius table encoded in code
- [ ] failing-or-passing tests for the 13 pitfalls that are testable without Herdr
- [ ] CLI help surface matches the spec verbs
- [ ] skill file forbids `--force`, `--include-local`, `--allow-reboot` unless the human said those words
- [ ] no `herdr machine` calls
- [ ] no `herdr update` on a pacman-detected host in any action command list

When the artifacts exist, stop and ask Grok to review.

---

## Reviewer brief (Grok)

### Role

You are the delegate reviewer, not a co-author. You do not rewrite the architecture unless it is unsafe. You produce a review with findings: **blocker / should-fix / nit**. Claude then patches.

Read, in order: this prompt, `watchbill-spec.md`, `docs/architecture.md`, `docs/roster.schema.json`, `src/watchbill/**`, `tests/**`, `skills/watchbill/SKILL.md`, `plugin/herdr-plugin.toml`.

### Review checklist

**Product**

- [ ] Name is Watchbill. No Muster collision.
- [ ] Verbs are roll / snap / secure / set / relieve. Relieve is generic; not “the herdr upgrader.”
- [ ] upgrade-agents and install-plugin exist as Action implementations, not new top-level commands.

**0.8.2 reality**

- [ ] Default transport is SSH + remote `herdr --session`, not `herdr --remote`.
- [ ] Live handoff is opt-in and errors on packaged installs. No silent fallback.
- [ ] #2064 attach-before-resume is in the `set` sequence.
- [ ] #3415 occupant-count guard protects `current.json`.
- [ ] #2297 — no `pane layout --current`.
- [ ] `layout.export` command is not trusted; process_info is.
- [ ] Durable keys are slot_id / human_id, never w1:p2.
- [ ] `session stop default` ≠ `server stop`.
- [ ] Omarchy/pacman path never calls `herdr update`.
- [ ] `agent prompt` is not sent to `blocked` agents.

**Architecture hygiene**

- [ ] Planners are side-effect free. exec.py is the mutation boundary.
- [ ] Action.blast_radius is data: `upgrade-agents` must not request session stop; `upgrade-herdr` must.
- [ ] Transport is swappable without touching planners.
- [ ] Roster schema 1 has `slot_id`, `human_id`, `role`, `agent_session`, `resume_argv`, `resume_prompt`, `allow_relaunch`, `live_ids`.
- [ ] Dry-run default on mutating commands.
- [ ] Self-pane skip.
- [ ] Rolling relieve + journal + `--resume`.
- [ ] Cockpit host skipped without `--include-local`.

**Tests**

- [ ] Classify: agent / watcher / shell / bridge.
- [ ] slot_id stable across pane_id change.
- [ ] working agent refused without `--force`.
- [ ] live mode + handoff unsupported → error.
- [ ] dry-run plan has no `session stop` / `pane close` / `agent prompt` scheduled for execution.
- [ ] pacman host action list does not contain `herdr update`.
- [ ] occupant guard rejects 6→1 snap as `current.json`.

**Scope control**

- [ ] This pass is architecture + skeleton, not a live SSH collector.
- [ ] No dependency on herdr-resurrect / herdr-hub / herdr-muster.
- [ ] No invented Herdr API.

### Review output format

Answer **PASS / FAIL / N/A** for every checklist item above. Then:

```
## Verdict
approve | approve-with-nits | request-changes

## Checklist
- Product / name: PASS|FAIL — note
- Relieve is generic: PASS|FAIL — note
- Transport default SSH not --remote: PASS|FAIL
- No live PTY adopt: PASS|FAIL
- No herdr update on pacman: PASS|FAIL
- Live handoff errors on packaged installs: PASS|FAIL
- #2064 attach-before-resume: PASS|FAIL
- #3415 occupant guard: PASS|FAIL
- #2297 no pane layout --current: PASS|FAIL
- Identity != pane id: PASS|FAIL
- session stop ≠ server stop: PASS|FAIL
- blocked agents not prompted: PASS|FAIL
- upgrade-agents does not session-stop: PASS|FAIL
- plugin install ≠ startup hook fired: PASS|FAIL
- Planners pure / exec mutates: PASS|FAIL
- Dry-run default: PASS|FAIL
- Tests cover classify / slot_id / guard / live-reject / pacman: PASS|FAIL
- Scope is skeleton not live SSH: PASS|FAIL

## Blockers
- file:line — what is wrong — what “done” looks like

## Should fix
- …

## Nits
- …

## 0.8.2 UNVERIFIED flags I want probed on a live box before MVP
- …
```

Do not implement while reviewing. Do not rewrite the architecture unless a FAIL is structural. If Claude marked `UNVERIFIED-0.8.2`, say whether the fallback is acceptable.

---

## Owner notes (not for the agents to invent)

- Personal repo. Suggested remote: `git@github.com:<YOU>/watchbill.git`.
- First milestone after architecture review: MVP CLI (`local` + `ssh_cli` transports, `roll`/`snap`/`secure park|dismiss`/`set --no-prompt`) against one cockpit + one Tailscale host.
- v1 adds plugin, skill wiring, pins, prompts, heartbeat, excerpts, `relieve` cold path including `upgrade-agents` and `restart-harness`.
- 0.9 is an adapter: import `herdr machine list` as extra `[[host]]` rows. Schema stays 1.
