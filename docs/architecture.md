# Watchbill architecture

Target: Herdr **0.8.2**, socket protocol **20**. Forward-compatible with 0.9
`machine` profiles as a host-discovery adapter only (extra `[[host]]` rows;
schema stays 1; nothing calls `herdr machine`).

Build contract: [kickoff-prompt.md](kickoff-prompt.md). Verified CLI facts:
[herdr-0.8.2-facts.md](herdr-0.8.2-facts.md). Roster schema:
[roster.schema.json](roster.schema.json).

> The contract names `watchbill-spec.md` as the product spec. That file was
> not present on the machine when this pass was built, so behaviour follows
> the contract's own goals, verbs, sequences and pitfalls. When the spec
> lands, the spec wins on behaviour and this document gets a diff pass.

## Hard constraint

Herdr owns the PTY master. There is no export-and-re-adopt-PTY API. Stopping
a server SIGHUPs every child. Live handoff (`herdr update --handoff`) exists
only for official `curl | sh` installs; Omarchy/pacman, mise, brew and Nix
cannot hand off. **Cold drain-and-resume is the default path.** Nothing here
is designed as if PTY adoption existed.

## Module map

```
src/watchbill/
  cli.py              parse, dispatch, exit codes. No herdr calls.
  hosts.py            hosts.toml → Fleet/Host (transport, sessions, start/attach commands, cockpit flag)
  paths.py            XDG locations (config, rosters, slots.json, journal)     [added]
  exitcodes.py        0/1/2/3/4 + RefusedPlan / UsageError / TransportError   [added]
  plan.py             Plan / Step / Refusal; MUTATING vs READONLY herdr verb tables; is_mutating()  [added]
  transport/
    base.py           HostSession protocol, CmdResult, NotImplementedInThisPass
    local.py          cockpit's own server
    ssh_cli.py        DEFAULT: ssh BatchMode + remote `herdr --session S …`
    ssh_socket.py     later: forwarded socket, JSON-RPC (layout.export/apply live here)
    herdr_remote.py   later, opt-in after doctor says versions match
  collect.py          gather() over a transport (read-only) + build_roster() (pure)
  classify.py         role from process_info argv + agent list; Allowlist
  roster.py           Roster/Occupant/Shape; load/save/merge; occupant guard; current.json
  slots.py            ULID slot_id store keyed by human_id, then agent_session
  resume.py           native resume argv table with verify column               [added]
  prompts.py          pinned > role template > excerpt-drafted resume prompts
  plan_secure.py      pure: roster → stand-down Plan (detach|park|fold|dismiss|host)
  plan_set.py         pure: roster → fall-in Plan (start, attach, shape, resume, relaunch, prompt)
  plan_relieve.py     pure: secure → action → set composition; live-mode gate
  actions/            maintenance-window plugins (Action protocol, BlastRadius, RemoteCmd)
    base.py upgrade_herdr.py restart_herdr.py omarchy_update.py upgrade_agents.py
    install_plugin.py restart_harness.py custom.py
  exec.py             the only module that mutates a live server; journal; placeholders; dry-run
  doctor.py           assess() pure + check() live: version, protocol, install flavor, handoff support
  journal.py          JSONL append; hosts_marked(run_id, "set-complete") for --resume
```

Four modules were added to the contract's map (`paths`, `exitcodes`, `plan`,
`resume`); each exists so that a planner or the executor does not have to
own a cross-cutting table. Nothing was removed.

### Rules

- **Planners are pure.** They import `transport` only for `herdr_argv()`
  (argv rendering, no I/O) so a dry-run prints the literal command.
- **`exec.py` mutates.** `Plan.scheduled()` is what it runs; without
  `--yes` that list contains no mutating step. `plan.is_mutating(argv)`
  classifies every herdr verb; unknown verbs fail closed as mutating. The
  SNAP/GUARD markers in a plan are performed by `cli._run_plan` around exec:
  a `pre-secure`/`pre-relieve` roster is written and the occupant guard
  evaluated before the first mutating step; a `post-*` snap is attempted
  after. Prompt steps carry `precondition="agent_status != blocked"` and
  exec checks it with `agent get` before typing anything.
- **Transports isolate SSH.** Collect, doctor and exec speak `HostSession`.
- **Actions declare blast radius** and return `RemoteCmd`s. They never call
  `session stop`. `plan_relieve` composes; `exec` applies.

## Data flow

```
hosts.toml ──► hosts.Fleet ──► transport.make_session(host, S) ──► HostSession
                                                                       │ read-only verbs
collect.gather ◄───────────────────────────────────────────────────────┘
      │  SessionFacts{status, snapshot, process_info[pane_id], excerpts}
      ▼
collect.build_roster ◄── slots.SlotStore (slot_id) ◄── classify (role) ◄── resume (argv) ◄── prompts
      │  Roster (schema 1)
      ├──► roster.write ──► rosters/<fleet>/<stamp>-<reason>.json ──guard──► current.json
      ▼
plan_secure / plan_set / plan_relieve(actions, doctor probes) ──► Plan{steps, refusals, notes}
      │
      ▼
exec.Executor.run(plan) ──► HostSession.herdr / .shell ──► journal.jsonl
```

## Identity

```
slot_id     ULID, minted once, ~/.local/share/watchbill/slots.json
human_id    host/session/workspace_label/tab_label/pane_label
live_ids    {workspace_id, tab_id, pane_id, terminal_id} — this generation only
```

Lookup order in `slots.assign`: `human_id`, then `(host, agent_session)`,
then mint. A conversation that moves to a relabelled workspace keeps its
slot. Duplicate workspace labels inside one session get `label#<number>`
(the real cockpit had two workspaces named `Work`). Pane labels are the
agent name when Herdr exposes one (UNVERIFIED-0.8.2 whether `agent list`
carries it), else `p<n>` in layout order.

Plans key every step on `slot_id`/`human_id`. Steps that need a pane id
carry `{pane:<slot_id>}` placeholders; `exec` resolves them from the JSON of
the `workspace create` / `pane split` that created the pane and rewrites
`live_ids` afterwards. `w1:p2` never appears as a plan key.

## Roles

First match: `bridge | agent | watcher | poller | server | editor | shell`.
`bridge` = any foreground `herdr` (`--remote`, `--session`, `session attach`,
`server`): catalogued, never parked or relaunched. `agent` = Herdr's own
detection (`pane.agent`), else argv basename in the kind table from
`herdr agent start --help`. Argv always comes from `pane process-info`;
`layout.export`'s `command` field is never read.

`allow_relaunch`: agents → via native resume; watcher/poller/server → only if
the full cmdline matches a glob in `allowlist.txt`; bridge/editor/shell → no.

## Transports

Default remote: `ssh -o BatchMode=yes -o ConnectTimeout=5 <target> -- herdr --session S …`
(remote side shell-quoted). Not `herdr --remote`: Omarchy's package has
reported `0.8.0` while speaking protocol 20 and `--remote` refuses on the
string mismatch. `herdr_remote` is opt-in: `make_session` refuses it unless
`Host.remote_verified` was set by a doctor version match.

Socket order (Herdr's own): `--session` → `HERDR_SOCKET_PATH` →
`HERDR_SESSION` → `~/.config/herdr/herdr.sock`; named sessions at
`~/.config/herdr/sessions/<name>/herdr.sock`. Watchbill always passes
`--session` explicitly, even for `default`.

Public remote commands Watchbill may emit (and nothing else):
`status server|client`, `agent list|get|start|prompt|send-keys|wait`,
`pane list|process-info|run|send-keys|send-text|split|close|read`,
`workspace list|create|close`, `tab list`, `session list|stop`,
`api snapshot`, `plugin list|install`, `integration install|status`.
`server stop` needs `--force-server-stop`. `layout.export`/`layout.apply`
are socket methods only (no CLI group in 0.8.2) and belong to `ssh_socket`.

## Sequence diagrams

### roll

```
cli.cmd_roll
  │ for host in fleet:
  ├─► collect.gather_host(host)
  │     └─► make_session(host, S).herdr("status","server","--json")      [r]
  │         herdr("api","snapshot")                                       [r]
  │         for pane: herdr("pane","process-info","--pane",<id>)          [r]  (never --current)
  │         [--excerpts] herdr("pane","read",<id>,"--source","recent")   [r]
  ├─► collect.build_roster(facts, slots, allowlist, pins, templates)      pure
  │     classify → slots.assign → resume.resume_argv → prompts.resolve
  ├─► roster.merge(current.json)   carry pins / allow_relaunch forward
  └─► print table | --json         exit 0, or 1 if a host was down
```

### secure park

```
cli.cmd_secure(mode=park, targets)
  ├─► roll (above)
  ├─► plan_secure.plan_secure(roster, fleet, opts)                        pure
  │     select(): targets → occupants; refusals for collision(--host),
  │               cockpit(--include-local); fleet-wide skips cockpit + notes
  │     SNAP pre-secure
  │     for occupant:
  │       self pane        → note, skip
  │       bridge           → note, skip
  │       agent blocked    → REFUSE (no override)      never type into a dialog
  │       agent working    → REFUSE [--force]
  │       agent            → pane send-text <pid> "/exit" ; pane send-keys <pid> enter ;
  │                          agent wait <pid> --until unknown --timeout 20000
  │       watcher/poller/server → pane send-keys <pid> ctrl-c   (UNVERIFIED key name)
  │     fold → workspace close <wsid> ; dismiss/host → session stop <S>   (never server stop)
  ├─► print plan; refused → exit 3; no --yes → exit 0 (dry-run)
  └─► --yes: exec.run(plan)  → journal → exit 0/1/4
```

### set

```
cli.cmd_set(targets, --from current.json)
  ├─► probes = doctor.check(host) per host  (or --probes-json)            [r]
  ├─► plan_set.plan_set(roster, fleet, opts)                              pure
  │     per (host, session):
  │       1 status server --json                                          [r]
  │       2 if not running: sh -c "<hosts.toml start>" ; wait status      [M]
  │       3 #2064 viewport: cockpit pane split --pane $HERDR_PANE_ID --direction down --no-focus
  │                         cockpit pane run {pane:viewport} ssh -tt <target> -- herdr session attach S
  │       4 shape by labels: workspace create --label L --cwd C --no-focus  → creates {pane:slot₀} and {ws:slot₀}
  │                          tab create --workspace {ws:slot₀} --label T --cwd C --no-focus → {pane:slot_t}
  │                          pane split {pane:slot_t} --direction right|down --cwd C → {pane:slotₙ}
  │       5 agent w/ session: agent start <name> --kind K --pane {pane:slot} -- claude --resume <id>
  │       6 unref agent:      agent start <name> --kind K --pane {pane:slot}
  │       7 allowlisted watcher: pane run {pane:slot} <argv…>
  │       8 prompt: agent wait <name> --until idle --timeout 90000 ;
  │                 agent prompt <name> <text> --wait --until working   (precondition: status != blocked)
  │       9 JOURNAL rewrite live_ids ; SNAP post-set (guard applies)
  └─► dry-run / --yes as above. exec fills {pane:…} from create/split JSON.
```

### relieve cold (upgrade-herdr on an official-installer host)

```
cli.cmd_relieve(action=upgrade-herdr, --mode cold)
  ├─► roster = current.json (or roll) ; probes = doctor per host
  ├─► plan_relieve.plan_relieve                                            pure
  │     action = actions.get(name, ctx)         blast = action.blast_radius()
  │     SNAP pre-relieve ; GUARD
  │     for host (rolling; skip cockpit w/o --include-local; skip set-complete on --resume):
  │       cmds = action.commands(host, probe)   ActionUnavailable → REFUSE
  │       park_steps(blast.park_roles ∩ park_kinds)          (secure park rules apply)
  │       sh -c "cp session.json session.json.watchbill-<run>"           #3415
  │       cmds with before_stop=True (install-plugin: bare `herdr plugin install`, no --session)
  │       blast.needs_session_stop → session stop <S>
  │       cmds with before_stop=False (official: herdr update ; mise: mise upgrade herdr ; pacman: REFUSE)
  │       verify → status server --json ; --expected-version note
  │       needs_session_stop → set_steps(parked, assume_running=False): start + attach + shape + resume
  │       JOURNAL set-complete <host>
  └─► dry-run / --yes ; exec stops at the first failed mutating step on a host; --resume continues
```

### relieve live (official-installer host)

```
relieve upgrade-herdr --mode live --host vps
  gate: every host that will run has probe.handoff_supported (flavor=official AND server flag)
  SNAP ; GUARD
  (no park, no session stop — handoff exists to keep the PTYs)
  herdr update --handoff            before_stop=True: the server must be running to hand off
  verify: status server --json ; --expected-version
  JOURNAL set-complete
```

### relieve live-rejected

```
cli.cmd_relieve(--mode live)
  ├─► probes: ser6 = {flavor: pacman, live_handoff_flag: true, handoff_supported: false}
  └─► plan_relieve: for host in scope:
        not probe.handoff_supported → raise RefusedPlan("ser6: live handoff unsupported (flavor=pacman …)")
      cli maps RefusedPlan → stderr message, exit 3. No cold fallback is planned or run.
```

### upgrade-agents (no session stop)

```
relieve upgrade-agents --kinds grok --host ser6
  blast = {park_roles: {agent}, park_kinds: {grok}, needs_session_stop: false, needs_client_attach: true}
  SNAP ; GUARD
  park grok  (send-text /exit ; enter ; wait unknown)
  cp session.json aside
  (no session stop)
  mise upgrade npm:@xai-official/grok   |  npm install -g @xai-official/grok@latest   (by probe.agent_flavors)
  herdr integration install grok
  verify: status server --json
  set_steps(parked, assume_running=True): viewport attach ; agent start grok-… --pane <live id> -- grok --resume <id> ; prompt
  JOURNAL set-complete
```

## Blast-radius matrix (encoded in `actions/`, tested in `test_blast_radius.py`)

| action | park | session stop | attach | notes |
|---|---|---|---|---|
| upgrade-herdr | agent | yes | yes | pacman → refused (never `herdr update`); mise/brew/official |
| restart-herdr | agent | yes | yes | no commands |
| omarchy-update | agent | yes | yes | `omarchy-update -y` only; pacman only |
| upgrade-agents | agent (kinds) | **no** | yes | per-kind by flavor + `integration install` |
| install-plugin | agent if startup hooks | if startup hooks | if startup hooks | enable ≠ hook fired |
| restart-harness | agent | yes | yes | no commands |
| custom | declared | declared | = session stop | `--may-reboot` declares; `--allow-reboot` is the operator gate |

## Occupant guard (#3415)

`roster.write` always keeps the timestamped file and retargets
`current.json` only when `occupant_guard(previous, current)` passes:
refuse if `current/previous ≤ 1 − drop_ratio` (0.5) or `current <
min_occupants` (2, enforced only once the previous roster had that many),
unless `--force`. Heartbeat snaps (`systemd --user` timer → `watchbill snap
-m heartbeat`) therefore cannot replace a good roster with a post-reboot
empty one.

## Pitfalls → where they are encoded

| # | pitfall | code | test |
|---|---|---|---|
| 1 | `--remote` version skew | `transport/ssh_cli.py` default | `test_pitfalls::test_01` |
| 2 | `pane layout --current` wrong (#2297) | every pane call passes `--pane <id>`; FakeSession asserts | `test_02`, `test_plan_set::test_no_pane_layout_current_anywhere` |
| 3 | `layout.export` omits `command` | `classify` reads only `process_info` | `test_03` |
| 4 | prefer `foreground_cwd` | `Occupant.effective_cwd` | `test_04` |
| 5 | pane ids generation-scoped | `slots.py`, placeholders | `test_05`, `test_slots::test_slot_ids_survive_pane_id_change` |
| 6 | resume needs a client (#2064) | `plan_set.attach_steps` before `agent start` | `test_06`, `test_plan_set::test_attach_precedes…` |
| 7 | `agent_session` missing (Codex unref) | `Occupant.unref`, fresh start + prompt | `test_07`, `test_plan_set::test_unref…` |
| 8 | empty `session.json` after reboot (#3415) | `roster.occupant_guard`, `cp session.json` step | `test_08`, `test_guard` |
| 9 | skip self pane | `plan_secure.park_steps` | `test_09` |
| 10 | nested Herdr = bridge | `classify` | `test_10` |
| 11 | label collision across hosts | `plan_secure.select` → `--host` or exit 3 | `test_11` |
| 12 | `agent prompt` into blocked → `agent_blocked` | park refuses blocked; prompt preceded by `wait --until idle` + precondition | `test_12` |
| 13 | Codex update dialog misread as idle (#3632) | `exec._post_prompt_check`: agent gone after prompt = failure | `test_exec::test_agent_exit_after_prompt_is_a_failure` |

## UNVERIFIED-0.8.2 (probe before MVP; each has a fallback)

| item | assumption in code | fallback |
|---|---|---|
| headless session start | `hosts.DEFAULT_START = "herdr --session {session} server"` | per-host `start =` in hosts.toml (Omarchy: `systemctl --user start herdr.service`) |
| TTY-less attach | none assumed; viewport = cockpit pane running `ssh -tt … herdr session attach S` | operator attaches by hand (NOTE step when not inside Herdr) |
| `send-keys` name for Ctrl-C | `"ctrl-c"` (`esc` is the only documented name) | `pane send-text` of `\x03` via ssh_socket, or `/exit`-style slash command per tool |
| park input for non-Claude agents | `/exit` (grok, cursor, opencode, hermes), `/quit` (codex, gemini) | `ctrl-c` twice |
| `hermes --resume <id>` | as contract | fresh start + prompt |
| `[[startup]]` manifest table | any `startup` key ⇒ hooks | operator `--startup-hooks` |
| agent name in `agent list` | `pane.name` / `pane.agent_name` if present | `p<n>` label; `agent start` name derived from kind + slot suffix |
| `splits` tree format in `layout.export` | not consumed; each extra pane splits off its tab's first pane by rect | capture one multi-pane layout live, then rebuild from the tree |
| `send-keys` name for Enter | `"enter"` (flagged unverified) | `pane send-text` with a trailing newline |
| `agent wait` target after `/exit` | pane id | `pane wait-output`, or poll `agent list` until the pane is gone |
| agent self-update argv (`grok upgrade`, `cursor-agent update`, `opencode upgrade`) | flagged unverified; fail closed if `--help` lacks it | mise/npm/brew path by flavor |

Verified on the live box after the first review and therefore *not* in the
table: `pane send-text`, `pane split [PANE_ID] --direction right|down --cwd
--no-focus`, `tab create --workspace --label --cwd --no-focus`, `agent get
<target>`, `agent wait --until unknown`, `plugin install --yes [--ref]`
(bare, no `--session`, before any stop), `integration install <kind>`,
`session.json` paths.

Review history: [reviews/2026-09-11-grok-review.md](reviews/2026-09-11-grok-review.md)
(approve-with-nits; every should-fix item is addressed in the follow-up
commit, the split-tree item partially: rect-based direction stays until a
multi-pane `splits` blob is captured).

## Exit codes

`0` ok · `1` partial (a host was down / a later host failed after an earlier
one completed) · `2` usage · `3` refused unsafe plan (any `Refusal`,
`RefusedPlan`, guard, never-emit violation) · `4` transport/action error.

## What this pass deliberately does not do

No live `ssh_cli`/`local` execution, no live collect, no exec against a real
Herdr. `NotImplementedInThisPass` marks every such call. Four
strict-xfail tests in `tests/test_red_mvp.py` turn green with the MVP.
