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

## Graph position
- PLAN: done (docs/mux-backends.md, the one-page dissent)
- PLAN-REVIEW: folded into CODE-REVIEW round 2 (design + code reviewed together; decision below)
- BRANCH: OVERRULED — commits go straight to main (personal private repo, single writer, user said "Commit to the repo"); recorded here rather than pretending a lane branch existed
- IMPLEMENT ⇄ PROVE: done; GATE green at 70ad647
- CODE-REVIEW: **current node** — Grok run-mtxfym2f-w2bcsa in progress; exit = review file with Verdict
- next edge: DISPOSITION (same day, every finding ACCEPTED/PARTIAL/NOTED/OVERRULED-with-reason) → IMPLEMENT⇄PROVE for should-fixes → CLOSE (run record, memory, report)

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
