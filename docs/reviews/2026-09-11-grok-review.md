# Watchbill architecture review — 2026-09-11 (Grok)

Reviewer: Grok (delegate, not co-author). No source was modified.

- Repo: `/home/holloway/Work/watchbill`, branch `main`, HEAD `ddde489e6b712deb17d84f692fbab8f2d053cb72`
- `watchbill-spec.md`: **absent**. Behaviour judged against `docs/kickoff-prompt.md` (Builder brief) as the contract.
- Tests: `uv run pytest -q` → **94 passed, 4 xfailed** (strict xfails in `tests/test_red_mvp.py`).
- Live probe (this box): `herdr 0.8.2` from `/usr/bin/herdr`, protocol 20. Used only to mark UNVERIFIED items true or false; not to run Watchbill against the server.

---

## Review checklist

**Product**

- [x] Name is Watchbill. No Muster collision. — **PASS** — product/CLI/plugin id `gw7523.watchbill`; tests reject `herdr-muster` / `kichel.muster`.
- [x] Verbs are roll / snap / secure / set / relieve. Relieve is generic; not “the herdr upgrader.” — **PASS** — `overhaul` is an alias of `relieve`; `upgrade-herdr` is an action, not a verb.
- [x] upgrade-agents and install-plugin exist as Action implementations, not new top-level commands. — **PASS**

**0.8.2 reality**

- [x] Default transport is SSH + remote `herdr --session`, not `herdr --remote`. — **PASS**
- [x] Live handoff is opt-in and errors on packaged installs. No silent fallback. — **PASS**
- [x] #2064 attach-before-resume is in the `set` sequence. — **PASS**
- [x] #3415 occupant-count guard protects `current.json`. — **PASS**
- [x] #2297 — no `pane layout --current`. — **PASS**
- [x] `layout.export` command is not trusted; process_info is. — **PASS**
- [x] Durable keys are slot_id / human_id, never w1:p2. — **PASS**
- [x] `session stop default` ≠ `server stop`. — **PASS**
- [x] Omarchy/pacman path never calls `herdr update`. — **PASS**
- [x] `agent prompt` is not sent to `blocked` agents. — **PASS** — park refuses with no override; set prefixes `agent wait --until idle`; Herdr itself rejects `agent_blocked`.

**Architecture hygiene**

- [x] Planners are side-effect free. exec.py is the mutation boundary. — **PASS** — planners call `make_session` only for `herdr_argv()` rendering.
- [x] Action.blast_radius is data: `upgrade-agents` must not request session stop; `upgrade-herdr` must. — **PASS**
- [x] Transport is swappable without touching planners. — **PASS**
- [x] Roster schema 1 has `slot_id`, `human_id`, `role`, `agent_session`, `resume_argv`, `resume_prompt`, `allow_relaunch`, `live_ids`. — **PASS**
- [x] Dry-run default on mutating commands. — **PASS**
- [x] Self-pane skip. — **PASS**
- [x] Rolling relieve + journal + `--resume`. — **PASS** — sequential hosts + `set-complete`; `--no-rolling` is currently a note only (should-fix).
- [x] Cockpit host skipped without `--include-local`. — **PASS**

**Tests**

- [x] Classify: agent / watcher / shell / bridge. — **PASS** (also poller / editor / server)
- [x] slot_id stable across pane_id change. — **PASS**
- [x] working agent refused without `--force`. — **PASS**
- [x] live mode + handoff unsupported → error. — **PASS**
- [x] dry-run plan has no `session stop` / `pane close` / `agent prompt` scheduled for execution. — **PASS**
- [x] pacman host action list does not contain `herdr update`. — **PASS**
- [x] occupant guard rejects 6→1 snap as `current.json`. — **PASS**

**Scope control**

- [x] This pass is architecture + skeleton, not a live SSH collector. — **PASS**
- [x] No dependency on herdr-resurrect / herdr-hub / herdr-muster. — **PASS**
- [x] No invented Herdr API. — **PASS** — `pane send-text`, `pane split`, `agent get`, `agent wait --until unknown` all exist on this 0.8.2 binary. Contract list was incomplete; architecture expanded it from live `--help`.

UNVERIFIED-0.8.2 rows in `docs/architecture.md` have fallbacks; all of them are acceptable for this pass (see last section). Live-accepted `--mode live` is the one design hole that should be fixed before anyone with an official installer uses it.

## Verdict
approve-with-nits

## Checklist
- Product / name: PASS — CLI `watchbill`, plugin `gw7523.watchbill`, no Muster/Fleet/Corral rename; `tests/test_skill_plugin.py:35` bans herdr-muster.
- Relieve is generic: PASS — `relieve`/`overhaul` take an Action (`upgrade-herdr`, `upgrade-agents`, `install-plugin`, …). No `upgrade-claude` verb (`tests/test_cli.py:34`, `tests/test_blast_radius.py:82`).
- Transport default SSH not --remote: PASS — `Host.transport` defaults to `ssh_cli` (`src/watchbill/hosts.py:44`); `SshCliSession.herdr_argv` is `ssh -o BatchMode=yes … -- herdr --session S …` (`src/watchbill/transport/ssh_cli.py:22-35`). `herdr_remote` is a later opt-in (`src/watchbill/transport/herdr_remote.py:1-6`).
- No live PTY adopt: PASS — architecture hard constraint; cold is default (`--mode cold`); packaged installs cannot hand off.
- No herdr update on pacman: PASS — `UpgradeHerdr.commands` raises `ActionUnavailable` on flavor=pacman (`src/watchbill/actions/upgrade_herdr.py:23-29`); even `allow_partial_pacman` emits `pacman -S`, never `herdr update`. Covered by `tests/test_blast_radius.py:38` and `tests/test_plan_relieve.py:55`.
- Live handoff errors on packaged installs: PASS — `handoff_supported = (flavor == "official" and flag)` (`src/watchbill/doctor.py:89`); `plan_relieve` raises `RefusedPlan` before composing a cold plan (`src/watchbill/plan_relieve.py:72-77`). CLI maps that to exit 3 (`tests/test_cli.py:73-75`). Pacman `live_handoff: true` is not trusted (`tests/test_pitfalls.py:109-117`).
- #2064 attach-before-resume: PASS — `attach_steps` runs before `agent start` (`src/watchbill/plan_set.py:70-92, 119-121`); viewport is `pane split --pane $HERDR_PANE_ID` then `ssh -tt … herdr session attach`. Tests: `tests/test_plan_set.py:20-31`, `tests/test_pitfalls.py:61-64`.
- #3415 occupant guard: PASS — `occupant_guard(6,1)` refuses (`src/watchbill/roster.py:232-234`); `write` keeps the timestamped file and does not retarget `current.json` (`src/watchbill/roster.py:251-263`, `tests/test_guard.py:15-37`). Relieve also copies `~/.config/herdr/session.json` aside (`src/watchbill/plan_relieve.py:55-56,120-125`) — path matches this box.
- #2297 no pane layout --current: PASS — collect uses `pane process-info --pane <id>` (`src/watchbill/collect.py:64`); FakeSession asserts no `--current` (`tests/conftest.py:145`); plans scanned in `tests/test_pitfalls.py:30-33`.
- Identity != pane id: PASS — `SlotStore.assign` keys on `human_id` then `(host, agent_session)` (`src/watchbill/slots.py:87-109`); `test_slot_ids_survive_pane_id_change` (`tests/test_slots.py:34-45`). Plans carry `{pane:<slot_id>}` placeholders, not `w1:p2` as keys.
- session stop ≠ server stop: PASS — dismiss/host/relieve emit `session stop <S>` (`src/watchbill/plan_secure.py:168`, `src/watchbill/plan_relieve.py:129-130`); `check_verbs_allowed` rejects `server stop` without `--force-server-stop` (`src/watchbill/plan.py:241-247`).
- blocked agents not prompted: PASS — park: `Refusal` with `override is None` (`src/watchbill/plan_secure.py:120-123`, `tests/test_plan_secure.py:29-32`). Prompt: `agent wait --until idle` then `agent prompt` with `precondition="agent_status != blocked"` (`src/watchbill/plan_set.py:173-178`). Herdr 0.8.2 rejects `agent_blocked` before input (verified `--help`).
- upgrade-agents does not session-stop: PASS — `BlastRadius(needs_session_stop=False)` (`src/watchbill/actions/upgrade_agents.py:40-43`, `tests/test_plan_relieve.py:32-36`).
- plugin install ≠ startup hook fired: PASS — default blast is no park / no session stop; bounce only if `startup_hooks` (`src/watchbill/actions/install_plugin.py:23-26`, `tests/test_blast_radius.py:24-29`). Skill says not to assume enable fired the hook.
- Planners pure / exec mutates: PASS — CONTRIBUTING + module docs; `exec.py` is the only `subprocess` caller; live transports raise `NotImplementedInThisPass`.
- Dry-run default: PASS — `_mut_flags` `--yes` (`src/watchbill/cli.py:100-102`); `_run_plan` returns 0 without exec unless `--yes` (`src/watchbill/cli.py:222-227`); `Plan.scheduled()` drops mutating steps (`src/watchbill/plan.py:183-187`). Plugin actions never include `--yes` (`plugin/herdr-plugin.toml:41-43`).
- Tests cover classify / slot_id / guard / live-reject / pacman: PASS — 94 passed, 4 xfailed. All 13 pitfalls have tests (`tests/test_pitfalls.py`).
- Scope is skeleton not live SSH: PASS — `LocalSession`/`SshCliSession` `.herdr`/`.shell` raise `NotImplementedInThisPass`; `tests/test_red_mvp.py` is strict xfail; `watchbill secure … --yes` exits 4 with that message (`tests/test_cli.py:60-62`).

## Blockers
(none)

## Should fix
- src/watchbill/actions/upgrade_herdr.py:18-19 and src/watchbill/plan_relieve.py:126-137 — `--mode live` on an official host still uses `needs_session_stop=True`, parks agents, `session stop`s, *then* emits `herdr update --handoff` (`tests/test_plan_relieve.py:26-29` only asserts the argv). Live handoff exists to keep PTYs; stopping the session first makes `--handoff` incoherent and likely fail (no server to hand off from). Done: live blast radius skips park + session stop; only `herdr update --handoff` + verify; cold stays as it is. Do not silently fall back to cold.
- src/watchbill/exec.py:147-150 and src/watchbill/cli.py:222-227 — SNAP and GUARD steps are no-op markers. The comment says “the CLI performs snaps/guards around exec”; `_run_plan` does not. `secure --yes` / `relieve --yes` will not write `pre-secure`/`pre-relieve` rosters or evaluate the occupant guard. Done: CLI snaps (reason=pre-*) before `Executor.run`, or exec actually performs those steps.
- src/watchbill/actions/install_plugin.py:28-35 plus src/watchbill/plan_relieve.py:127-137 — `plugin install` is `via="herdr"` (so `herdr --session S plugin install`). Contract: install does **not** need a running server. With `--startup-hooks` the plan stops the session *first*, then talks to that session’s socket. Done: `via="shell"` (bare `herdr plugin install … --yes`), and if hooks require a bounce, install **before** `session stop` (or after start), not against a dead socket.
- src/watchbill/collect.py:158-163 — pane labels are not uniqued (workspaces are, `_dedupe_workspace_labels` at `collect.py:89-98`). Two panes with `name`/`agent_name` both `claude` in the same tab mint the same `human_id` and therefore the same `slot_id`. Done: suffix `claude#2` (or always `p<n>` plus a separate display name).
- src/watchbill/actions/upgrade_agents.py:40-43 — omitting `--kinds` sets `park_kinds=None` (park **every** agent) while `commands()` only upgrades kinds present on the probe. Done: `park_kinds` equals the kinds that will actually be upgraded.
- src/watchbill/cli.py:160-170 / src/watchbill/doctor.py:95-116 — `doctor.check` is called with `kinds=()` so live `probe.agent_versions` is empty; `upgrade-agents` without `--kinds` then `ActionUnavailable`. Done: probe kinds from the roster (or `command -v` of known agent bins).
- src/watchbill/plan_set.py:137-144 — shape rebuild is a star of splits off pane 0 from rect x, and never emits `tab create`. Live 0.8.2 *does* have `herdr tab create --workspace <id> --label --cwd --no-focus`. Done: one `tab create` per extra tab; split using the actual split tree (`shape.splits`) rather than “everything off the first pane”.
- src/watchbill/plan_relieve.py:68,72-77 — `--no-rolling` only changes a note; hosts are always sequential. Live-mode all-or-nothing also inspects the cockpit host even when it will be skipped without `--include-local`. Done: apply the live gate only to hosts that will actually run; either implement `--no-rolling` or drop the flag.
- src/watchbill/exec.py:147-176 — `step.precondition` is never evaluated. Blocked-prompt safety is “wait --until idle, then hope Herdr returns `agent_blocked`”. Done: skip or refuse the prompt step when live `agent get` says `blocked`.
- src/watchbill/transport/__init__.py:18-30 — `transport = "herdr_remote"` in hosts.toml is used with no doctor version-match gate. Done: `make_session` refuses `herdr_remote` unless a probe says versions match (keep ssh_cli default).

## Nits
- src/watchbill/plan_secure.py:129 — `pane send-keys <pid> enter` is not flagged `unverified`; only `ctrl-c` is (`plan_secure.py:39`). Help documents `esc` only. Probe `enter` vs `Enter` vs `return` before MVP.
- src/watchbill/plan_secure.py:131-132 — park wait is `agent wait <pane_id> --until unknown`. `--until unknown` is valid (verified `--help`). Whether TARGET may be a pane id rather than an agent name after `/exit` is still unproven.
- src/watchbill/hosts.py:36 — `DEFAULT_START = "herdr --session {session} server"` is correctly UNVERIFIED; `herdr server --help` on this box lists only stop/reload subcommands. Per-host `start = systemctl --user start herdr.service` is the right Omarchy fallback.
- src/watchbill/actions/upgrade_agents.py:30-31 — `SELF_UPDATE` for grok (`grok upgrade`), cursor (`cursor-agent update`), opencode (`opencode upgrade`) is marked unverified. Keep the raise-if-unknown path.
- src/watchbill/roster.py:1-10 — docstring mentions `watchbill snap --validate`; CLI has no such flag. jsonschema is a dev extra only (`pyproject.toml:20-24,39-43`).
- src/watchbill/cli.py:130-152 — `_roll` always `slots.save`s. Fine for identity, but a first `watchbill snap` against unimplemented transports writes a 0-occupant roster and the guard allows it (`previous <= 0`). Refuse to retarget `current.json` when every host is down.
- skills/watchbill/SKILL.md:22-34 — `--force` / `--include-local` / `--allow-reboot` require the human to have said the word. Good. Plugin manifest correctly omits mutating one-clicks.
- `tab create` is listed UNVERIFIED in architecture.md:300; flags are now known (see last section). Drop that row or replace with “JSON result of tab create”.

## 0.8.2 UNVERIFIED flags I want probed on a live box before MVP
Already listed in docs/architecture.md — fallbacks acceptable unless noted:

- Headless session start (`herdr --session S server`) — **fallback OK** (hosts.toml `start=`). On this box `herdr server --help` does not show a no-subcommand run; do not ship the default without a probe.
- TTY-less attach — **fallback OK** (NOTE step when Watchbill is not inside Herdr; viewport `ssh -tt … session attach` when it is). #2064 still requires a client.
- `send-keys` name `ctrl-c` — **fallback OK** (`send-text` of `\x03`, or per-tool slash). Probe `herdr pane send-keys <id> ctrl-c` on a sacrificial pane.
- `send-keys` name `enter` — **not flagged in code**. Probe before MVP; fallback: `pane run` is documented as “sends text and Enter in one call” but that runs a command, not `/exit`.
- Park slash commands (`/exit` vs `/quit` per kind) — **fallback OK** (`ctrl-c` twice). Claude `/exit` is verified; others are not.
- `hermes --resume <id>` — **fallback OK** (fresh start + prompt). Binary was mid-install when facts.md was written; still the one resume row that is `verified=False` (`src/watchbill/resume.py:32-33`).
- `[[startup]]` table shape — **fallback OK** (`--startup-hooks`). Do not parse inventively; any `startup` key ⇒ hooks is fine.
- Agent name in `agent list` — **fallback OK** (`p<n>` + `kind-slot` for `agent start`).
- `layout.apply` via socket — **fallback OK** (`workspace create` + `pane split`, both verified CLI). Schema confirms `workspace.create` success is `{root_pane, tab, workspace}` so `exec._record_created` looking at `root_pane.pane_id` is schema-true. Still capture one live CLI JSON blob for `workspace create` and `pane split` (schema has no `pane_split` result type; likely `pane_info` `{pane, type}`).

Add these (not in the architecture table, now that this box was probed):

- `herdr plugin install` with `--session S` while that session is **stopped** — expect fail; install must not use the session socket.
- `agent wait` TARGET after park: pane id vs agent name vs “unknown”.
- `tab create --workspace <id> --label --cwd --no-focus` JSON result (flags **verified** on 0.8.2; architecture still says “not emitted”).
- Self-update argv: `grok upgrade`, `cursor-agent update`, `opencode upgrade` (mark fail-closed if `--help` has no such subcommand).
- `herdr --session S server` as a headless start on a host with no user unit.

Verified on this box, so they can leave the UNVERIFIED table: `pane send-text`, `pane split --pane/--direction/--cwd/--no-focus`, `agent get`, `agent wait --until unknown`, `session.json` paths (`~/.config/herdr/session.json` and `sessions/<name>/session.json`), `tab create` flags, `agent start --kind --pane -- <argv>`, `plugin install --yes`, `integration install`.
