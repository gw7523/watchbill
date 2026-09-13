# graph-loop state — watchbill

ROLE=OWNER
ASSIGNMENT=Extend Watchbill from Herdr-only to a per-host multiplexer axis (herdr | tmux | cmux) with the same roll/secure/set/relieve semantics, architecture + skeleton only (no live transports).
BOX=rig2 (cockpit)
SCALE=FULL
HOUSE=.graph-loop/house.md
SUCCESS=`uv run pytest -q` green (119 passed, 4 xfailed) AND Grok round-2 review verdict is approve or approve-with-nits with every should-fix dispositioned.
DONE-LAYER=merged (main, pushed to gw7523/watchbill; repo is private)
GATE=uv run pytest -q
PROVE=tests/test_mux.py (tmux parser against the captured 3.7c fixture; tmux/cmux plan argv; capability refusals; MANUAL steps) + tests/test_pitfalls.py (herdr guarantees unchanged)
ADVERSARY=grok-build bridge write-mode run with the Reviewer brief (house overlay)

## Claim and exit criteria
- Claim: the mux backend indirection preserves every round-1 herdr guarantee and adds tmux (verified) and cmux (docs-only, fail-closed) without a roster schema bump.
- Exit: round-2 review dispositioned; should-fixes patched with a test at the named failure point; GATE green; docs (mux-backends.md, facts files, README, AGENTS.md) in the same commits.

## Oracles per claim
| claim | oracle |
|---|---|
| herdr guarantees unchanged | tests/test_pitfalls.py, test_plan_*.py, test_exec.py (unchanged assertions) |
| tmux argv verified | tests/test_mux.py vs docs/tmux-3.7-facts.md |
| cmux fails closed | test_cmux_backend_fails_closed_and_manual_steps |
| schema 1 additive | tests/test_schema.py validates a roster with mux fields |
| design acceptable | Grok round-2 verdict file docs/reviews/2026-09-11-grok-review-2.md |

## Budget
- attempts: 3 per should-fix item (cap → escalate to Jack with compacted failure)
- scope: no live execution; no new verbs beyond reload-config / upgrade-mux alias
- wall-clock: today (2026-09-11)

## Graph position — lane 1 (mux axis): CLOSED
- PLAN → docs/mux-backends.md (the one-page dissent; ACCEPTED by the reviewer)
- BRANCH: OVERRULED — commits go straight to main (private repo, single writer, user said "Commit to the repo")
- IMPLEMENT ⇄ PROVE → GATE green
- CODE-REVIEW → docs/reviews/2026-09-11-grok-review-2.md, verdict **request-changes**, 5 blockers
- DISPOSITION → docs/reviews/2026-09-11-disposition-2.md (same day; 5 blockers fixed, 8 should-fix accepted, 1 overruled with reason, nits split accepted/noted)
- CLOSE → run record appended; docs in the same commits as the behaviour

## Graph position — lane 2 (MVP transports): CLOSED
- Human gate opened ("let's get started on the ... implementation")
- PLAN → contract milestone 1: local + ssh_cli transports, live roll/snap/secure/set
- IMPLEMENT ⇄ PROVE → `transport.run_argv` is the single spawn point; local + ssh_cli execute;
  live roll/snap/status/doctor verified against the real cockpit; tests/test_live.py is the oracle
- PROVE also probed the UNVERIFIED-0.8.2 list on an ISOLATED `--session wbprobe` server
  (started, probed, stopped, deleted; the `default` session with four live agents was never touched)
  → five herdr corrections, each now pinned by a test
- CLOSE → run record appended

## Lane 3 (rehearsal: park → stop → start → resume on an isolated session): OPEN
- Human gate: "start the development" (2026-09-13), after agreeing the local isolated rehearsal before ser6.
- SUCCESS: a real haiku Claude in an isolated `wbrehearse` herdr session on rig2 is parked, the session
  is stopped, Watchbill brings the session back, resumes the SAME conversation with its original flags,
  and the resumed agent answers a question only the original conversation knows (a codeword).
- PROVE: the codeword appears in the resumed pane (`pane read --source visible`); agent_session.value
  unchanged across the cycle.
- GATE: uv run pytest -q
- Isolation: rehearsal runs with XDG_CONFIG/DATA/STATE_HOME in a scratch dir and its own hosts.toml naming
  only the `wbrehearse` session; the `default` session is never in its roster.
- Budget: 3 attempts per failure cause; a failure that could touch `default` is STOP + escalate.
- Pre-run findings (probe/read, fixed before the run):
  1. `herdr --session S server` stays in the FOREGROUND (exec would block, then kill it on timeout) → start with `setsid -f`
  2. status on a stopped session is rc=0 + `"running":false` → the start wait must poll for running:true
  3. resume argv dropped the agent's original flags (e.g. --dangerously-skip-permissions, --model)
  4. exec used a fixed 30s subprocess timeout for steps whose own --timeout is 90s
  5. the `reach` step used the mux status verb, which fails on a dead tmux server before `start` runs

## Next lane (not started)
- MVP end-to-end across two boxes: needs `~/.config/watchbill/hosts.toml` naming a Tailscale
  host, and a human decision to run a mutating verb with `--yes` against real agents.
  Everything up to `--yes` is exercised; nothing has ever parked a live agent.
- Unprobed: park slash commands other than Claude `/exit`, `hermes --resume`, every cmux verb
  (needs a Mac), tmux approval-prompt tails per agent kind (needs the Macs' real dialogs).

## Decisions
- mux is a per-host axis orthogonal to transport; one mux per host (why: a host has one PTY owner; mixing muxes per session would double every planner path)
- schema stays 1, `mux` optional with default herdr (why: readers of old rosters keep working; bump only for changed/removed required fields)
- tmux agent status is heuristic and `unknown` needs --force (why: false idle would type /exit into a dialog; false blocked only costs a refusal)
- cmux relaunch is a MANUAL step (why: no CLI relaunch is documented; inventing one is what the contract forbids)
- upgrade-herdr kept as alias of upgrade-mux (why: the contract's checklist names it)

## Learned traps (also in house NEVER)
- grok-build bridge read-only sandbox fails on /run/containerd/containerd.sock here → run with --write and audit git status
- `omarchy-update --help` opens the confirm TUI; only `-y` is automation-safe
- argparse: a subcommand option with dest "cmd" clobbers the verb slot
