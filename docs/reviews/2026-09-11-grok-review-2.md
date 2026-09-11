# Watchbill architecture review 2 — 2026-09-11 (Grok) — multiplexer axis

Reviewer: Grok (delegate, not a co-author). No existing source was modified. This file is the only write.

- Repo: `/home/holloway/Work/watchbill`, branch `main`, HEAD `70ad6478114fb734163799e9289098b24a8e1026`
- Contract: `docs/kickoff-prompt.md` (Builder brief). Dissent: `docs/mux-backends.md`. Facts: `docs/tmux-3.7-facts.md`, `docs/cmux-facts.md` (cmux CLI also re-fetched from https://cmux.com/docs/api on 2026-09-11).
- Round 1: `docs/reviews/2026-09-11-grok-review.md` (approve-with-nits). Architecture claims every should-fix was patched (`docs/architecture.md:344-347`); herdr-path follow-ups still hold. This round judges the mux axis on top of that.
- Tests: `uv run pytest -q` → **119 passed, 4 xfailed** (strict xfails in `tests/test_red_mvp.py`). Exit 0. (`pyproject.toml` already sets `addopts = "-q"`, so a second `-q` hides the summary line; count is 119 progress dots + 4 `x`.)

Read in the order asked. Live tmux/cmux was not driven; argv and plans were judged against the facts docs, the captured 3.7c fixture, and a dry construction of `plan_set` / `plan_relieve` on the mixed-fleet test roster.

---

## Review checklist

**Product**

- [x] Name is Watchbill. No Muster collision. — **PASS** — unchanged; plugin id `sfl.watchbill`; `upgrade-mux` is an action alias, not a rename.
- [x] Verbs are roll / snap / secure / set / relieve. Relieve is generic; not “the herdr upgrader.” — **PASS** — `reload-config` and `upgrade-mux` are actions; verbs unchanged (`tests/test_cli.py:30-34`).
- [x] upgrade-agents and install-plugin exist as Action implementations, not new top-level commands. — **PASS** — plus `reload-config` / `upgrade-mux` in the same registry (`src/watchbill/actions/__init__.py:17-27`).

**0.8.2 reality**

- [x] Default transport is SSH + remote `herdr --session`, not `herdr --remote`. — **PASS** — transport still defaults to `ssh_cli`; mux is a prefix inside it (`src/watchbill/hosts.py:46-54`, `src/watchbill/transport/ssh_cli.py:34-36`). `herdr_remote` + non-herdr mux is rejected (`src/watchbill/hosts.py:101-102`, `src/watchbill/transport/__init__.py:29-31`).
- [x] Live handoff is opt-in and errors on packaged installs. No silent fallback. — **PASS** — plus tmux/cmux `live_handoff == "never"` raises `RefusedPlan` before composing (`src/watchbill/plan_relieve.py:79-86`, `tests/test_mux.py:198-201`).
- [x] #2064 attach-before-resume is in the `set` sequence. — **PASS** — skipped when `caps.needs_viewport` is false (`src/watchbill/plan_set.py:91-92`, `tests/test_mux.py:178-179`); herdr path unchanged (`tests/test_plan_set.py:20-31`).
- [x] #3415 occupant-count guard protects `current.json`. — **PASS** — unchanged (`src/watchbill/roster.py:225-242`, `src/watchbill/cli.py:239-246`).
- [x] #2297 — no `pane layout --current`. — **PASS** — herdr still `process-info --pane <id>` (`src/watchbill/mux/herdr.py:54`); tmux/cmux have no such flag; plans scanned (`tests/test_pitfalls.py:30-33`, `tests/test_mux.py:189`).
- [x] `layout.export` command is not trusted; process_info is. — **PASS** — tmux argv comes from `ps -t <tty>` (`src/watchbill/mux/tmux.py:97-121`, `src/watchbill/collect.py:6-8`).
- [x] Durable keys are slot_id / human_id, never w1:p2. — **PASS** — tmux `%N` / cmux surface uuid live only in `live_ids` (`src/watchbill/roster.py:29-32, 68`; `tests/test_mux.py:117`).
- [x] `session stop default` ≠ `server stop`. — **PASS** on herdr (`src/watchbill/mux/herdr.py:108-112`). On tmux both methods return `kill-server` (`src/watchbill/mux/tmux.py:182-186`) by the dissent’s noun map (Watchbill session = tmux socket). The herdr guarantee is intact; the tmux mapping is gated (see blockers).
- [x] Omarchy/pacman path never calls `herdr update`. — **PASS** — `UpgradeMux` is the same class as `upgrade-herdr`; pacman still raises or emits `pacman -S`, never `herdr update` (`src/watchbill/actions/upgrade_herdr.py:39-45`, `tests/test_blast_radius.py:38-56`).
- [ ] `agent prompt` is not sent to `blocked` agents. — **FAIL** on tmux (herdr still PASS). Park still refuses `blocked` with no override (`src/watchbill/plan_secure.py:149-151`). Set still stamps `precondition="agent_status != blocked"`. Exec’s tmux check calls `looks_blocked(None, screen)` (`src/watchbill/exec.py:109-110`), which drops kind-specific Claude patterns (`Do you want to`, `❯ 1. Yes`) and only keeps the generic `[y/n]` family (`src/watchbill/detect.py:20-36`). A Claude approval dialog that the collector would mark `blocked` is invisible to the prompt-time guard.

**Architecture hygiene**

- [x] Planners are side-effect free. exec.py is the mutation boundary. — **PASS** — backends are argv builders; `exec` is still the only runner. `mux_step` calls `make_session` only for argv rendering (`src/watchbill/plan_secure.py:80-90`).
- [x] Action.blast_radius is data: `upgrade-agents` must not request session stop; `upgrade-herdr` must. — **PASS** — `upgrade-mux` reuses that blast (`src/watchbill/actions/upgrade_herdr.py:23-24`); `reload-config` parks nothing (`src/watchbill/actions/reload_config.py:20-21`).
- [x] Transport is swappable without touching planners. — **PASS** as a seam (`hs.mux_argv` prefixes `herdr --session` / `tmux -L` / `cmux`). **FAIL as implemented for `RemoteCmd.via="herdr"`** — `plan_relieve.emit` treats `via in ("herdr", "mux")` as “strip argv[0] and run through the host mux” (`src/watchbill/plan_relieve.py:145-146`), so herdr-shaped action/verify argv is rewritten as `tmux …` / `cmux …` on mixed hosts.
- [x] Roster schema 1 has `slot_id`, `human_id`, `role`, `agent_session`, `resume_argv`, `resume_prompt`, `allow_relaunch`, `live_ids`. — **PASS** — `mux` is optional with default `herdr` on host / shape / occupant (`docs/roster.schema.json:40-41, 88, 110, 189`; not in `required`). Runtime dataclasses default the same way (`src/watchbill/roster.py:68, 87, 99`); a schema-1 roster without `mux` still loads.
- [x] Dry-run default on mutating commands. — **PASS** — `--yes` still required (`src/watchbill/cli.py:99-101, 236-237`). `MANUAL` steps are `mutating=True`, so dry-run does not wait on the operator (`src/watchbill/plan_secure.py:83-84`; `src/watchbill/plan.py:189-193`).
- [x] Self-pane skip. — **PASS** — unchanged (`src/watchbill/plan_secure.py:139-141`).
- [x] Rolling relieve + journal + `--resume`. — **PASS** — `--no-rolling` was dropped (usage error, `tests/test_cli.py:86-88`); rolling remains the only mode.
- [x] Cockpit host skipped without `--include-local`. — **PASS** — unchanged; live-mode gate still ignores the skipped cockpit (`tests/test_plan_relieve.py:35-40`).

**Tests**

- [x] Classify: agent / watcher / shell / bridge. — **PASS** — plus tmux fixture roles (`tests/test_mux.py:106-118`).
- [x] slot_id stable across pane_id change. — **PASS** — unchanged (`tests/test_slots.py`).
- [x] working agent refused without `--force`. — **PASS** — plus `unknown` on tmux/cmux (`tests/test_mux.py:161-165, 239-240`).
- [x] live mode + handoff unsupported → error. — **PASS** — herdr packaged + tmux/cmux never (`tests/test_plan_relieve.py:18-23`, `tests/test_mux.py:198-201, 249-250`).
- [x] dry-run plan has no `session stop` / `pane close` / `agent prompt` scheduled for execution. — **PASS** (`tests/test_exec.py:19-32`, `tests/test_mux.py:158`).
- [x] pacman host action list does not contain `herdr update`. — **PASS**
- [x] occupant guard rejects 6→1 snap as `current.json`. — **PASS**

**Scope control**

- [x] This pass is architecture + skeleton, not a live SSH collector. — **PASS** — `NotImplementedInThisPass` still on live `mux`/`shell` (`src/watchbill/transport/local.py:27-33`, `ssh_cli.py:43-49`). Mux work is pure argv + parsers + planner routing.
- [x] No dependency on herdr-resurrect / herdr-hub / herdr-muster. — **PASS** — TPM is invoked as a path on the tmux host, not vendored.
- [x] No invented Herdr API. — **PASS** — herdr backend verbs are the round-1 set.

**Mux axis (this round)**

- [x] (1) Dissent acceptable: mux as a per-host axis orthogonal to transport; schema 1 kept with additive optional `mux`. — **PASS** — see Verdict.
- [ ] (2) Every herdr guarantee from round 1 still holds through backend indirection. — **FAIL** — `--current`, pacman `herdr update`, dry-run, identity, and herdr session-vs-server still hold. Tmux prompt-time blocked check does not. `via="herdr"` is not orthogonal to mux.
- [ ] (3) tmux argv vs 3.7c facts; derived status; cwd-scoped `--continue`; kill-server gating; select-layout. — **FAIL** — builders match 3.7c; cold `set`/`relieve` composition does not (placeholders, duplicate `new-session`, unnamed first window, `kill-server` vs blast radius). Details below.
- [x] (4) cmux fails closed on undocumented verbs; MANUAL for quit/relaunch; nothing material invented as a CLI verb. — **PASS** with nits (JSON field-name guessing, `escape` as interrupt).
- [ ] (5) actions: reload-config live; install-plugin on tmux via TPM; upgrade-mux per mux. — **FAIL** — `reload-config` is right. TPM path is the right idea but not socket-scoped, and `--startup-hooks` still means “kill the tmux server”. `upgrade-mux` on tmux/cmux cannot execute as documented (verify argv, `kill-server` gate, duplicate `new-session`).

---

## Verdict
request-changes

The dissent is the right shape: one mux per host, orthogonal to transport, schema 1 kept by adding optional `mux` with default `herdr`. The herdr backend still behaves like round 1. The tmux *builders* match the 3.7c capture. The cmux backend, as a docs-only stub, mostly fails closed and uses `MANUAL` instead of inventing a relaunch CLI.

What is not acceptable is the tmux (and mixed-host action) *composition*: a cold `set`/`relieve` plan that looks right in tests will not resolve placeholders, will try to `new-session -s` the same name twice after a harness bounce, will run `tmux integration install` / `tmux status server --json`, and will be refused by exec for the `kill-server` that the blast radius just emitted. Those are not live-probe unknowns; they are in the plans the tests already build and then do not execute.

---

## Checklist
- Product / name: PASS — CLI `watchbill`, plugin `sfl.watchbill`, mux names herdr/tmux/cmux only (`src/watchbill/hosts.py:35`).
- Relieve is generic: PASS — `upgrade-mux` aliases `upgrade-herdr`; `reload-config` is an action (`src/watchbill/actions/__init__.py:17-21`).
- Transport default SSH not --remote: PASS — `SshCliSession.mux_argv` is `ssh … -- <mux prefix> …` (`src/watchbill/transport/ssh_cli.py:34-36`).
- No live PTY adopt: PASS — tmux/cmux `live_handoff="never"`; live mode errors (`src/watchbill/mux/tmux.py:21`, `cmux.py:15`, `src/watchbill/plan_relieve.py:82-83`).
- No herdr update on pacman: PASS — `UpgradeMux.commands` (`src/watchbill/actions/upgrade_herdr.py:39-45`).
- Live handoff errors on packaged installs: PASS — doctor still requires official+flag (`src/watchbill/doctor.py:89, 121-122`); mux `never` overrides (`src/watchbill/doctor.py:121-122`).
- #2064 attach-before-resume: PASS — herdr only; tmux/cmux skip (`src/watchbill/plan_set.py:91-92`).
- #3415 occupant guard: PASS — unchanged.
- #2297 no pane layout --current: PASS — no `--current` in herdr or tmux argv.
- Identity != pane id: PASS — `%N` / surface uuid are `live_ids.pane_id`.
- session stop ≠ server stop: PASS (herdr) / mapped (tmux) — herdr still `session stop` vs `server stop`; tmux both are `kill-server`, exec-gated (`src/watchbill/plan.py:251-255`).
- blocked agents not prompted: FAIL — tmux exec guard drops kind (`src/watchbill/exec.py:109-110`).
- upgrade-agents does not session-stop: PASS — blast unchanged (`src/watchbill/actions/upgrade_agents.py:46-47`).
- plugin install ≠ startup hook fired: PASS on herdr default (no hooks → no bounce). FAIL leak: `--startup-hooks` still requests session stop on a tmux host (`src/watchbill/actions/install_plugin.py:23-26`), which is `kill-server`.
- Planners pure / exec mutates: PASS
- Dry-run default: PASS — including MANUAL (mutating, not scheduled without `--yes`). `--yes` does **not** skip MANUAL (`src/watchbill/exec.py:178-180`, `tests/test_mux.py:267-275`).
- Tests cover classify / slot_id / guard / live-reject / pacman: PASS — 119 passed, 4 xfailed. New coverage in `tests/test_mux.py` is argv/plan-shape only; it never runs `Executor` against a tmux plan, which is why the blockers below are green.
- Scope is skeleton not live SSH: PASS
- (1) Dissent acceptable: PASS — mux ⊥ transport; schema 1 additive `mux`.
- (2) Round-1 herdr guarantees through indirection: FAIL — blocked-prompt + `via="herdr"` rewrite.
- (3) tmux facts / status / continue / kill-server / select-layout: FAIL — builders PASS; cold composition FAIL.
- (4) cmux fail-closed / MANUAL / not invented: PASS
- (5) reload-config / TPM install-plugin / upgrade-mux per mux: FAIL — reload-config PASS; the other two do not execute as documented.

---

## Blockers

- `src/watchbill/plan_set.py:73-76, 132-138, 162-164, 185-188` + `src/watchbill/exec.py:70-87` + `src/watchbill/plan_secure.py:85-89` — tmux cold `set` cannot fill `{pane:<slot>}` / `{ws:<slot>}`. `start_steps` records the first pane under `creates="boot:{host}/{session}"`. Shape rebuild then treats that pane as `booted_slot`, adds the *slot_id* to `created`, and emits `split-window -t {pane:<slot>}`, `new-window -t {ws:<slot>}`, and `send-keys -t {pane:<slot>}`. Exec only writes `pane_map["boot:…"]` and `pane_map["ws:boot:…"]`. Observed on the mixed-fleet roster: `set1.start` creates `boot:mac/default`; `set1.split.*` and `set1.tab.alpha.logs` still target `{pane:01M…}` / `{ws:01M…}`. Separately, `mux_step`’s `__poll__` branch drops `placeholders=True` and bakes the unresolved `{pane:…}` into the `sh -c` wait loop at plan time, so even a later resolver would not see the flag. Done: the start step’s `creates` must be the booted slot_id (or exec must alias `boot:` → that slot and `ws:<slot>`); `__poll__` must keep `placeholders=True` so `{pane:…}` inside the remote loop is substituted.

- `src/watchbill/plan_relieve.py:174-187` + `src/watchbill/plan_set.py:129-138` — tmux cold relieve emits two `new-session -s <first ws>`. After `kill-server`, `start_steps` already created `alpha`. `set_steps(..., assume_running=True, live=None)` then skips the `booted_slot` assignment (that block is inside `if not running`) and emits `mac.slots1.ws.alpha` = another `new-session -d -s alpha`. Observed in a constructed `plan_relieve(upgrade-mux)`: `mac.set1.start` and `mac.slots1.ws.alpha` are both `new-session -s alpha`. Done: after a tmux start in the same plan, `set_steps` must reuse the boot pane (pass `booted_slot`, or `assume_running` must not mean “invent the first workspace again”).

- `src/watchbill/plan_relieve.py:145-146, 163-166` + `src/watchbill/actions/upgrade_agents.py:70-71` + `src/watchbill/actions/base.py:94-98` — `RemoteCmd.via="herdr"` is not herdr-shaped once the host mux is tmux/cmux. `emit` does `mux_step(..., *c.argv[1:])`, so `("herdr", "integration", "install", "claude")` becomes `tmux -L default integration install claude`, and default verify becomes `tmux -L default status server --json`. Observed on `relieve upgrade-agents --kinds claude` for `mux=tmux`. `reload-config` avoids this only because it overrides `verify` (`src/watchbill/actions/reload_config.py:30-32`). Done: `via="herdr"` must mean the herdr CLI (or be omitted on non-herdr hosts); `via="mux"` is the host backend. `BaseAction.verify` must use `backend.status_argv()`, as `ReloadConfig` already does.

- `src/watchbill/mux/tmux.py:182-186` + `src/watchbill/plan.py:254-255` + `src/watchbill/plan_relieve.py:153-161` + `src/watchbill/exec.py:131-137` — `kill-server` is both the blast-radius session stop *and* the forbidden server-stop. `plan_relieve(upgrade-mux|restart-harness|omarchy-update)` on tmux always emits `("kill-server",)`. `Executor.run` then refuses the **whole** plan unless `--force-server-stop`. The skill forbids that flag unless the human said “server stop” (`skills/watchbill/SKILL.md:29`), and CLI help still describes it as `herdr server stop` (`src/watchbill/cli.py:56`). `tests/test_mux.py:168-171` asserts the gate for dismiss; `tests/test_mux.py:207-214` asserts `mac.stop1` exists for upgrade-mux and never runs `check_verbs_allowed` / `Executor`. Done: blast-radius `needs_session_stop` on tmux must be allowed without a second flag (the analog of herdr `session stop`); keep `--force-server-stop` for unplanned/out-of-window kills. Or, if the gate is load-bearing, the planner must refuse up front, CLI/skill/docs must require the flag for tmux cold windows, and tests must run exec against that plan.

- `src/watchbill/exec.py:89-113` + `src/watchbill/detect.py:20-36, 31-36` — pitfall 12 regresses on tmux at the moment of prompt. Collector status is kind-aware (`src/watchbill/collect.py:208-210`) and will mark `Do you want to` / `❯ 1. Yes` as `blocked`. Exec re-reads the screen with `looks_blocked(None, …)`, so those lines do not match. Combined with `agent_wait_idle` only waiting for “not a shell” (`src/watchbill/mux/tmux.py:168-171`) and never for quiet time (the comment claims “the exec-side poll re-reads window_activity”; exec does not), a tmux `set` prompt can type into a Claude approval dialog. Done: pass the occupant kind into `looks_blocked`; wait until quiet **and** no prompt pattern before `send-keys` of the resume text (as `docs/mux-backends.md:134-137` already specifies).

---

## Should fix

- `src/watchbill/mux/tmux.py:136-137, 146-147` + `src/watchbill/plan_set.py:190-194` — `select-layout -t alpha:edit` is the 3.7c argv, but cold start is `new-session -s alpha` with **no** `-n edit`. The first window is not named `edit`, so the one path that uses `layout_reapply` (`not ws_live`) targets a window that does not exist. Extra tabs are fine (`new-window -n`). Done: `new-session … -n <first tab label>` (or `rename-window`), or target `session:{window_index}`. Also do not apply a full-window layout string when `wanted_slots` restored fewer panes than the layout describes.

- `src/watchbill/mux/tmux.py:108-115` — `parse_process_info` keeps a process only when `STAT` contains `+` **and** `pid != pane.pid`. That is correct for “shell with a child agent”. After `respawn-pane -k` the watcher *is* `pane_pid`; collect will see no foreground argv and classify `shell`, so the next `set` will not relaunch it. Done: if no `+` child exists, treat `pane_pid` / `pane_current_command` as the occupant.

- `src/watchbill/collect.py:176-182, 233-238` — cwd-uniqueness is counted on `pane.foreground_cwd or pane.cwd` (tmux: always pane path) and looked up on process-info cwd. If those differ, two agents of one kind sharing a real cwd still get `claude --continue`. Done: count and lookup the same key (`effective_cwd`).

- `src/watchbill/actions/install_plugin.py:23-26, 31-38` — tmux install is the right *idea* (TPM + `source-file`, live, `unverified=True`). Two holes: (1) `--startup-hooks` still parks agents and requests session stop, i.e. `kill-server`, which the design says never happens for tmux plugins; (2) `~/.tmux/plugins/tpm/bin/install_plugins` talks to whatever `tmux` the script invokes (usually the default socket), while Watchbill’s session is `-L S`. Done: ignore `startup_hooks` when `host.mux == "tmux"`; run TPM under `tmux -L S …` (or `TMUX=` pointing at that server). Verify should not be `herdr plugin list` (`install_plugin.py:50-51`).

- `src/watchbill/mux/tmux.py:127-128` + `src/watchbill/plan_set.py:219-232` — `send-keys -l` of a multiline `resume_prompt` (the default excerpt template contains newlines) is likely several Enter-separated submissions. Done: strip/replace newlines, or send line-by-line without Enter until the last line.

- `src/watchbill/doctor.py:95-123` vs `docs/mux-backends.md:36-37` / `docs/cmux-facts.md:32-37` — design says doctor reports cmux socket access mode (`cmuxOnly` vs `allowAll`); `check` never does. Remote `roll` failing closed on the default is acceptable; the operator currently gets a generic status failure. Done: parse `capabilities --json` for the access mode (field name UNVERIFIED-LIVE) and put it on `Probe`.

- `skills/watchbill/SKILL.md` + `src/watchbill/cli.py:32, 56` — skill and parser description are still “Herdr 0.8.2 fleet” / “allow `herdr server stop`”. A chief-of-staff agent following the skill will not know `mux =` or that tmux dismiss is `kill-server`. Done: one paragraph on the mux axis; describe `--force-server-stop` as covering `herdr server stop` **and** `tmux kill-server`.

- `src/watchbill/detect.py:1-11, 48-50` vs `docs/mux-backends.md:96-100` — design said the heuristic marker goes in `tasking`. Collect only writes a tasking note for ambiguous continue (`src/watchbill/collect.py:252`), not for `agent_status_source=heuristic`. False-idle is the dangerous direction; operators cannot see that a tmux `idle` is guessed.

- `src/watchbill/plan_secure.py:51` + `src/watchbill/plan_relieve.py:50` — `force_server_stop` is accepted on the options objects and never consulted by the planners; only exec looks at it. Dry-run of tmux dismiss *prints* `kill-server` as a mutating step and only dies on `--yes`. Done: planner-level Refusal when a tmux plan contains `kill-server` without the flag (if you keep the gate) so dry-run matches exec.

---

## Nits

- `docs/tmux-3.7-facts.md:23-27` lists shorter `-F` strings than `src/watchbill/mux/tmux.py:26-31` (code adds `session_id`, `window_id`, `pane_tty`). The fixture `tests/fixtures/tmux-3.7c.txt` matches the code, not the facts doc. Update the facts doc to the captured formats so the next reviewer does not think the parser is untested.

- `docs/architecture.md:33-63` module map still omits `actions/reload_config.py` and still describes ssh_cli as “remote `herdr --session S`” only.

- `src/watchbill/hosts.py:38-50` — `start`/`attach` still default to herdr even when `mux = "tmux"|"cmux"`. Harmless for tmux (`start_steps` uses `session_start`) and cmux (MANUAL), confusing in a generated `hosts.toml`.

- `src/watchbill/mux/__init__.py:16-18` — `get(name, **kw)` forwards `mux_options` into the backend constructor. `HerdrBackend` takes no kwargs; a herdr host with leftover `mux_options` raises `TypeError`.

- `src/watchbill/collect.py:144` — `idle_after_s=30.0` is hardcoded; `Host.mux_options` / `TmuxBackend.idle_after_s` is unused at collect time.

- `src/watchbill/mux/tmux.py:82-87` — status uses `#{window_activity}` (window-scoped, as in the facts). A quiet agent sharing a window with a noisy pane looks `working` (safe). A blocked agent in a quiet window whose dialog does not match patterns looks `idle` (unsafe). Worth a comment next to `status_is_parkable`.

- `src/watchbill/mux/cmux.py:101-102` — `interrupt` sends `escape` because ctrl-c is not in the documented key set. Honest, but it will not stop a watcher. cmux watchers are currently unclassified (no process info) so this is mostly dormant.

- `src/watchbill/mux/cmux.py:50-80, 164-176` — `parse_snapshot` / `created_ids` guess `uuid`/`id`/`title`/`cwd`/…. Marked UNVERIFIED-LIVE; fail-soft to empty. Acceptable for a docs-only backend; do not treat a green parser test on hand-built JSON as a Mac probe.

- `src/watchbill/mux/cmux.py:19-20` — `READONLY` includes `list-notifications`, `list-status`, `list-log`, `sidebar-state`. Those **are** on https://cmux.com/docs/api (facts.md’s CLI box is incomplete). Not invented. Live docs also say `list-panels` “List all surfaces in the current workspace”, which sits poorly next to mapping panels → tabs (`docs/cmux-facts.md:16`). Probe on a Mac before trusting the tab/pane split.

- `src/watchbill/actions/omarchy_update.py:22-25` — no explicit `mux == "cmux"` refuse; brew-flavored Macs fail the pacman check anyway. Fine; one line would match the matrix.

- `src/watchbill/mux/cmux.py:104-105` — `new-workspace` ignores label/cwd because the CLI has no such flags. Correct. cmux `set` therefore cannot restore workspace titles; a NOTE on the step would save a future “why is everything untitled” report.

- `docs/mux-backends.md:112` vs `src/watchbill/plan_relieve.py:153-161, 174-178` — design table is “park, brew cask upgrade, MANUAL relaunch, restore-session”. Code is MANUAL quit, then cask upgrade, then MANUAL launch + `restore-session`. The extra quit is better; update the table.

- Round 1 nits still open and still acceptable: herdr `enter`/`ctrl-c` key names, park slash commands other than Claude `/exit`, `hermes --resume`, `DEFAULT_START`.

---

## Round 1 should-fixes (claimed patched)

All still hold on the **herdr** path. Not re-litigated:

| Round 1 item | Still true? |
|---|---|
| live `--handoff` does not session-stop first | yes — `before_stop=live` (`src/watchbill/actions/upgrade_herdr.py:51-53`), `tests/test_plan_relieve.py:26-32` |
| SNAP/GUARD performed by CLI | yes — `src/watchbill/cli.py:239-246` |
| `plugin install` via shell, before stop | yes — `tests/test_plan_relieve.py:43-48` |
| duplicate pane names suffixed | yes — `tests/test_pitfalls.py:120-128`; tmux uses the same uniquing (`src/watchbill/collect.py:193-195`) |
| `upgrade-agents` park_kinds | yes — `tests/test_plan_relieve.py:55-64` |
| doctor kinds from roster | yes — `src/watchbill/cli.py:209-210, 312` |
| `tab create` emitted | yes — herdr and tmux (`src/watchbill/plan_set.py:171-178`) |
| live gate only on hosts that run | yes |
| `--no-rolling` dropped | yes — usage error (`tests/test_cli.py:86-88`) |
| exec checks blocked before prompt | yes on herdr (`tests/test_exec.py:93-110`); **regressed on tmux** (blocker) |
| `herdr_remote` needs doctor match | yes — `tests/test_pitfalls.py:132-138` |

---

## 0.8.2 / mux UNVERIFIED flags I want probed

Already listed in `docs/mux-backends.md` / facts — fallbacks acceptable unless noted:

**tmux (probe on a Mac with the owner’s config, throwaway `tmux -L wbprobe`)**

- Approval-prompt patterns per kind — **fallback OK** (false `blocked` = refuse; the remaining hole is false `idle` + exec dropping kind, which must be fixed in code first). Capture one real Claude / Codex / Grok / Gemini dialog tail.
- `#{window_activity}` vs per-pane activity — confirm whether a split window with one noisy pane marks the sibling `working`.
- `new-session -d -s <ws> -c <cwd>` default window name (base-index, automatic-rename) — needed to make `select-layout -t <ws>:<tab>` true or false.
- `select-layout` with a 3.7c `window_layout` after N `split-window`s of only a *subset* of the original panes.
- `respawn-pane -k` on a pane whose shell is still alive; then `ps -t` / `pane_pid` identity (see should-fix).
- TPM `install_plugins` against `-L <not-default>` on the Macs.
- `send-keys -l` with embedded newlines (resume prompt).
- `source-file ~/.tmux.conf` as a live reload that keeps PTYs (facts claim it; worth one attach-and-check).

**cmux (UNVERIFIED-LIVE until a Mac probe; do not invent while waiting)**

- `list-workspaces` / `list-panels` / `list-pane-surfaces --json --id-format uuids` field names. Especially: is `list-panels` actually surfaces (live CLI reference wording)?
- `surface resume show --json --surface <id>` shape (`command` vs `argv` vs `resume`) and whether it implies kind.
- Socket access mode on the owner’s Macs (`cmuxOnly` vs `allowAll`) and whether SSH `roll` fails closed as designed.
- `new-split` positions / whether `--workspace` / `--surface` globals are required to split the intended panel.
- `restore-session` after a manual relaunch (may already have restored on quit).
- `brew upgrade --cask cmux` as the upgrade path.
- No process-info / no screen-read / no reload / no plugin / no quit CLI — code already returns `None` / MANUAL; probe only to confirm they are still absent.

**herdr leftovers from round 1, still worth a probe before MVP**

- Headless `herdr --session S server`; `send-keys` names `enter` / `ctrl-c`; park slash commands other than Claude `/exit`; `hermes --resume`; `agent wait` TARGET after park.

Verified this round against published cmux CLI reference (not invented): `list-workspaces`, `new-workspace`, `close-workspace`, `list-panels`, `list-pane-surfaces`, `new-split {left,right,up,down}`, `send --surface`, `send-key` key set, `surface resume show --json`, `restore-session`, `capabilities --json`, `identify`, `--socket`, `--id-format`, plus sidebar/notification list commands that facts.md omitted. Code does not emit `local-tmux` / `ssh-tmux` / `mosh-tmux` / `hooks setup` / `surface resume set`.
