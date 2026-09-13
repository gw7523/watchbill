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

## Lane 3 (rehearsal: park → stop → start → resume on an isolated session): CLOSED — PROVE green twice
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

- Attempt 1 (2026-09-13) → FAIL at resume, 16 steps ran first (park, stop, detached start, up-wait, workspace, config check).
  Findings, each now fixed before attempt 2:
  6. `herdr agent start` PREPENDS the canonical executable: args only after `--` (was: resume + the word "claude" sent as a prompt)
  7. isolating with XDG_CONFIG_HOME redirects herdr's own socket dir → WATCHBILL_HOME override
  8. ROOT CAUSE of the failed resume: a server started from inside a Claude session inherits CLAUDECODE /
     CLAUDE_CODE_CHILD_SESSION / CLAUDE_CODE_SESSION_ID; agents in it become non-persisting child sessions
     (no transcript → `No conversation found with session ID`). Core use case (an agent drives Watchbill) → the
     local transport now scrubs agent-session env from everything it spawns.
  9. herdr restores the workspace layout from session.json on restart; the planner assumed an empty server and
     created a duplicate workspace → exec reconciles creates against the restarted server by label
  10. agent start had no deadline of its own and raced exec's 30s timeout; a failed start now records the pane tail
  Classification: attempt 1 was partly INSTRUMENT (the harness polluted the subject) and partly real product bugs.

- Attempt 2 → run 1 green (21 steps, 0 failed, 6s). Run 2 REFUSED: relieve planned from the post-set roster,
  written while the resumed agent was `working` → 11. relieve now plans from a live roll. Also 12: secret-name
  matching by substring flagged GIT_AUTHOR_NAME → segment match.
- Attempt 2 run 2 (retried) → green. PROVE both runs: same agent_session 8cd76f33…, same argv
  `claude --model haiku --dangerously-skip-permissions --resume 8cd76f33…`, one workspace, codeword recalled.
- `default` session never addressed: every rehearsal argv carried `--session wbrehearse`. Session deleted after.
- Owner added mid-lane: record prior configuration (permission mode, plugins/hooks, …) and resume with the same
  config → agentconfig.py; proven by the argv check above.

## Lane 4 (portability + scope, owner direction 2026-09-13): CLOSED
- Owner: ser6 is personal work only; do not use its SFL distrobox for now. Watchbill must be deployable in other
  environments (this machine's SFL seat) and reach another machine's seat.
- Built: `exclude` (catalogue-only, no override; refuses any window that would stop/close what it runs in),
  `exec_prefix` (one install runs commands inside an environment, local or over ssh), no home-dir assumption for
  herdr's session.json (asks herdr), setsid→nohup start fallback, `env_captured` flag.
- PROVE: tests/test_portability.py; live read-only on ser6: distrobox pane EXCLUDED, park plans personal agents
  only, restart-harness REFUSED (would kill the excluded pane). nohup start form proven to detach on Linux.
- Deliberately not done: any probe inside a distrobox (owner deferred it).

## Lane 5 (ser6 live, owner-approved 2026-09-13): CLOSED — step A green, step B green twice
- Human gate: "approved to stop ser6 sessions (including herdr) to test. Nothing critical running there. Do not
  (yet) upgrade herdr." → restart-harness only (no action commands); no upgrade-mux / omarchy-update.
- Scope split: the distrobox pane moves from `exclude` (protected) to new `ignore` (never managed; may end when
  its session stops; the plan says so). "Don't use the distrobox" still holds: never parked, never restored.
- Step A: throwaway `wbrehearse` session ON ser6 over SSH, a haiku Claude AND a Grok (grok /exit unverified;
  real grok runs on ser6). PROVE as lane 3, per agent. Unknowns it answers: remote detached start, PATH of
  agents started by a server launched over non-interactive ssh, grok park/resume.
- Step B: ser6 `default`, the real fleet: park 2 claude + 1 grok, stop session, start, reuse restored layout,
  resume each with recorded config, --no-prompt (never inject text into real conversations).
  PROVE: each agent's agent_session unchanged and argv flags identical before/after.
- Budget: 3 attempts per cause; any failure mid-step-B → stop, report with the journal, recover by hand.
- Step A result: 3 runs over SSH on ser6 wbrehearse. Run 1 failed at Grok's resume prompt (agent_prompt_stalled:
  freshly resumed Grok dropped the submission) → exec retries a stalled prompt once after settling. Runs 2 and 3:
  29 steps, 0 failed. PROVE: Claude same session + flags all runs, codeword answered twice. Grok same session +
  flags all runs, codeword answered conclusively once (run 3's answer not distinguished before cleanup).
  Grok `/exit` park verified live. Remote setsid start clean (no session-identity env).
  Found, not ours: ser6 `~/.local/bin/tl-context-guard` is a dangling symlink (token-lean moved out of
  personal-config) while ser6 Claude settings call it on every prompt.
- Step B config: separate WATCHBILL_HOME naming only ser6 default (no cockpit → no viewport split on rig2),
  --no-prompt.

- Pre-step-B probe: the recorded config showed `trusted=False` for the omarchy-plugins Claude. Tested on a throwaway
  ser6 session before touching it: Claude 2.1.252 with --dangerously-skip-permissions starts there without a dialog.
  Also added: restore-phase failures are scoped to the occupant (host marked set-partial), parking stays host-fatal.
- Step B run 1: 31 steps, 0 failed. PROVE: w3 claude 3bfed0ba…, w4 claude 0bad6b62…, w6 grok 01a06d73… unchanged;
  flags unchanged; panes/workspaces/cwds unchanged; ignored distrobox pane ended as planned.
  FINDING: the server restarted over ssh had SSH_CONNECTION and no desktop session → sessionenv.py.
- Step B run 2: 31 steps, 0 failed. Same PROVE, and the new server has WAYLAND_DISPLAY=wayland-1, DISPLAY=:0,
  XDG_SESSION_TYPE=wayland, XDG_CURRENT_DESKTOP=Hyprland, no SSH_CONNECTION.
- Not ours, reported: ser6 ~/.local/bin/tl-* are dangling (token-lean left personal-config) while ser6 Claude
  settings run tl-context-guard on every prompt; ser6 herdr.service hardcodes a missing ~/.local/bin/herdr.
- Test artifacts (rehearsal transcripts, grok session, session-env entries) deleted on ser6 after confirming codewords.
- Follow-ups: tmux server start does not apply the session prelude yet; the #2064 viewport step never proved
  necessary (claude + grok, local + ssh) → candidate to make opt-in.

## Lane 6 (owner: fix ser6's two issues, re-test, then a tmux example): CLOSED
- ser6 token-lean: cloned gw7523/agent-skills on ser6 and ran its installer (--check reviewed first): tl-* relinked,
  skills taken over from personal-config marks as intended. No repo edited (other agents active in both repos on rig2).
- ser6 herdr.service: stopped the 203/EXEC loop (245,396 restarts), ran personal-config's own
  tools/linux/herdr-autostart/install.sh (unit == repo, enabled, live server left alone).
- Re-test: ser6 default with a haiku test agent in /tmp/wb-hooktest (trust accepted for that tmp folder),
  start = systemctl --user start herdr.service. 39 steps, 0 failed. PROVE: 3 real agents same ids + flags; test agent
  same id + flags, codeword recalled, no hook error; herdr.service active, MainPID /usr/bin/herdr server,
  WAYLAND_DISPLAY=wayland-1, no SSH_*. Test agent exited and its workspace closed afterwards.
- tmux example (examples/tmux/, rig2 local, haiku Claude + Grok in /tmp + a `watch` window):
  run 1 FAIL safely at verify (ran between kill-server and start) → verify moved after the restart.
  Recovery via `watchbill set` from the pre-window roster worked, but exposed a broken tmux idle condition
  (`[ a ] [ b ]`, string-tested only) → fixed + conditions now executed in tests.
  Next runs: post-prompt check used the unresolved placeholder → prompt Enter skipped; park then appended /exit to
  the pending text (Claude answered instead of exiting; exit wait failed; host stopped before kill-server — safe).
  → resolved pane in the check; clear pending input (tmux C-u, herdr send-text 0x15, both verified) before /exit and
  prompts. Then watch window lost: stop windows restored only parked agents → restore every occupant the stop ends.
  Final: 2 runs, 37 steps, 0 failed; layout string identical, `watch -n 5 date` relaunched, both agents resumed with
  flags + --continue, codewords recalled (Claude every run; Grok 3 runs captured). Demo torn down.
- Test agents: transcripts live under ~/.claude/projects/-tmp-* and ~/.grok/sessions/%2Ftmp%2F* (not in /tmp);
  they age out with each tool's retention. Trust was accepted for /tmp/wb-tmux-a (rig2) and /tmp/wb-hooktest (ser6).

## Lane 7 (owner: proceed; Mac mini over Tailscale; containers for agents without accounts): CLOSED
- #2064 viewport now opt-in (mux_options.attach_before_resume).
- Mac mini (holloway@100.105.4.100, ssh_options -i personal key): docs/macos-facts.md. herdr 0.9 (protocol 22)
  green 4 runs via a launchd GUI-domain throwaway job; tmux over SSH green 2 runs (mechanics; agent logged out).
  Fixed live: ssh_options; BSD/macOS tmux process probe; agent argv from its own process (caffeinate); herdr pre-prompt
  clear by pane; Claude Remote Control park guard + opt-in disconnect. Cleaned up: test agent exited, wbmac servers
  stopped, launchd job booted out, plist removed.
- cmux: installed CLI differs from the published docs (cmux-facts.md note). App not running; not probed live.
- Agent CLIs without accounts (docs/agent-cli-facts.md): flag tables for codex, cursor, gemini, opencode from --help;
  credential flags redacted in recorded argv. Throwaway Ollama container (qwen2.5:1.5b, localhost) → codex --oss and
  opencode live on an isolated herdr session: run 1 FAIL safely (codex /quit not submitted) → exit waits nudge Enter
  once; runs 2 and 3 green, continuity proven from each CLI's session storage (model too small for recall). Also fixed:
  seat env exported into reused panes (opencode lost OPENCODE_CONFIG). Gemini and Cursor: no account, flag tables only.
- Cleaned: test herdr session, Ollama container and image, test codex rollout and opencode session, /tmp test dirs.
- cmux live: the app is not running and SSH access needs a socket password (cmux Settings or CMUX_SOCKET_PASSWORD).

## Next lane (not started)
- Park commands still unverified: codex (/quit), gemini, cursor, opencode, hermes.
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
