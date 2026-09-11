# Disposition — Grok review round 2 (mux axis)

Review: [2026-09-11-grok-review-2.md](2026-09-11-grok-review-2.md) · Verdict
**request-changes** · 5 blockers, 9 should-fix, 14 nits.
Dispositioned same day, as the operating contract requires. GATE
(`uv run pytest -q`) green at 131 tests, 6 of them live against a real
multiplexer.

## Blockers — all ACCEPTED and fixed

| # | Finding | Fix | Proof |
|---|---|---|---|
| 1 | tmux cold `set` could not fill `{pane:}`/`{ws:}`: the start step created `boot:<host>/<session>` while the shape emitted the slot_id; `__poll__` also dropped `placeholders` | the tmux start step's `creates` **is** the booted occupant's slot_id, so one step fills both `{pane:slot}` and `{ws:slot}`; `mux_step` carries `placeholders` into POLL steps | `test_executor_runs_a_whole_tmux_cold_set` — the first test that runs the Executor over a tmux plan; the fake server asserts no placeholder ever reaches tmux |
| 2 | tmux cold relieve emitted two `new-session -s <ws>`; the second fails as a duplicate | `set_steps` owns reach → start → attach → shape, so relieve no longer starts the session separately | `test_tmux_relieve_cold_starts_the_session_once` |
| 3 | `RemoteCmd(via="herdr")` was replayed against the host's mux, so `herdr integration install` became `tmux integration install` and the default verify became `tmux status server --json` | `via` is `mux`/`shell` only; `BaseAction.verify` uses `backend.status_argv()`; the integration refresh is emitted only on a herdr host; `emit` asserts `argv[0] == backend.name` | `test_no_herdr_only_verbs_reach_a_tmux_host` |
| 4 | tmux `kill-server` was both the declared session stop and the forbidden server stop, so exec refused the whole plan unless the human said a flag the skill forbids | `Step.planned_stop` marks the blast radius's own stop; the gate refuses only an *unplanned* `kill-server`. herdr `server stop` stays gated unconditionally | `test_tmux_dismiss_is_a_planned_kill_server` |
| 5 | pitfall 12 regressed on tmux: exec re-read the screen with `kind=None` so kind-specific dialogs never matched, and the idle wait only checked "a binary is up" | `Step.agent_kind` reaches `looks_blocked`; the tmux idle poll now requires the window to be **quiet** for `idle_after_s` as well | `test_blocked_check_uses_the_occupants_kind` — the executor refuses and never sends the keys |

## Should fix

| Finding | Disposition |
|---|---|
| `select-layout` targeted a window the cold start never named | **ACCEPTED** — `new-session … -n <first tab label>`; a layout is re-applied only when every pane it describes was restored |
| `parse_process_info` dropped the occupant after `respawn-pane -k` (the command becomes `pane_pid`) | **ACCEPTED** — when no `+` child exists, the pane's own non-shell process is the occupant |
| cwd-uniqueness counted and looked up different keys | **ACCEPTED** — one `_cwd_key` / `_effective_cwd` used by both |
| `install-plugin`: `--startup-hooks` still bounced a tmux server; TPM talked to the default socket; verify was `herdr plugin list` | **ACCEPTED** — hooks apply to herdr only; TPM runs under a PATH shim pinning `tmux -L <socket>`; verify is per-mux |
| multi-line resume prompt submits at every newline under `send-keys -l` | **ACCEPTED** — non-herdr prompts are flattened to one line |
| doctor does not report the cmux socket access mode | **NOTED** — the field name is UNVERIFIED-LIVE; on the probe list, not guessed |
| skill and CLI help still say "Herdr fleet" / "`herdr server stop`" | **ACCEPTED** — skill has a "One mux per host" section; the flag's help covers both muxes |
| a guessed (heuristic) status is invisible to the operator | **ACCEPTED** — roster `tasking` carries `status: heuristic` |
| planner should refuse `kill-server` without the flag so dry-run matches exec | **OVERRULED** — blocker 4 removed the mismatch instead: a planned stop is sanctioned in both places, so dry-run and exec already agree. Gating it again would re-create the deadlock the blocker described. |

## Nits

**Accepted:** facts doc now shows the exact `-F` strings the parser sends (and
says the fixture matches them); architecture module map lists
`reload_config.py` and the generalised ssh_cli prefix; `mux.get()` ignores
unknown `mux_options` instead of raising; `idle_after_s` comes from the host's
`mux_options`; a `window_activity` comment records which direction is unsafe;
`omarchy-update` refuses cmux explicitly; cmux `new-workspace` emits a NOTE
that the workspace comes back untitled; the cmux facts CLI box gained the
sidebar/notification commands it had omitted; the mux-backends table now shows
the extra MANUAL quit.

**Noted, not changed:** `hosts.toml` `start`/`attach` still default to herdr
strings (unused on tmux/cmux, cosmetic); cmux `interrupt` sends `escape`
because no ctrl-c key is documented (dormant — cmux watchers are unclassified
without process info); cmux parsers guess field names (already UNVERIFIED-LIVE
and fail-soft — a green parser test on hand-built JSON is **not** a Mac probe).

## Round-1 leftovers the reviewer wanted probed — done this session

Three of them were probed on a live 0.8.2 box (isolated `--session wbprobe`
server, stopped and deleted afterwards) and **each found a real bug**:

| Probed | Result |
|---|---|
| `send-keys` key names | `ctrl-c` is **rejected** (`invalid_key`); `C-c` is the accepted name. `enter` and `Enter` both work. **Was a latent runtime failure on every watcher park.** |
| `agent wait` target after park | an exited agent is *absent*, not `unknown`: `agent wait`/`agent get` answer `agent_not_found`. `--until unknown` was the wrong oracle; the park wait is now a poll until `agent get` fails. |
| headless `herdr --session S server` | **works**, detached. `Capabilities.headless_start` is now `verified`. |

Two more corrections came out of the same session: `agent wait` is
**level-triggered**, so waiting only for `idle` hangs the full timeout on an
agent that settled to `done` (now waits for idle/done/blocked); and
`pane read --source recent` is empty on a settled pane, so excerpts use
`--source visible`. All five are pinned by
`test_herdr_verbs_match_what_was_probed_on_0_8_2` and by the live
`test_herdr_verbs_against_an_isolated_probe_session`.

Still unprobed and unchanged: park slash commands other than Claude's
`/exit`, `hermes --resume`, and every cmux verb (no Mac reachable from this
cockpit).
