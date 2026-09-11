# graph-loop house overlay — watchbill

GATE: uv run pytest -q            # must be green before every commit; 4 strict xfails in tests/test_red_mvp.py are expected until the MVP
PROVE: the failing test or probe that pins the claim (tests/test_*.py); for mux verbs, an argv assertion against the matching docs/*-facts.md entry; never "the suite is green" alone
ADVERSARY: grok-build bridge, write-mode run (the bridge's forced read-only sandbox dies on /run/containerd on Omarchy):
  node ~/.claude/plugins/cache/xai-grok-build/grok-build/0.2.1/scripts/grok-bridge.mjs run --background --fresh --write '<Reviewer brief from docs/kickoff-prompt.md + scope>'
  audit `git status` after every run; the only file the reviewer may create is docs/reviews/<date>-grok-review*.md
REVIEW_DIR: docs/reviews
BOXES:
  - rig2: cockpit (Omarchy, herdr 0.8.2 pacman, tmux 3.7c for throwaway probes; cmux cannot be probed here)
  - ser6: Omarchy worker (herdr), reachable over Tailscale — not touched in the skeleton pass
  - macs: tmux today, cmux candidate; live probes for docs/cmux-facts.md happen there
INTEGRATE: GATE on rig2 + the MVP live roll against one cockpit + one Tailscale host (contract milestone 1)
ESCALATE_ROUTINE: the human (Jack) — no orchestrator; single OWNER lane
ESCALATE_HUMAN: Jack, for: making the repo public, any --yes run against a live mux, cmux probes on a Mac, MVP go-ahead ("implement MVP next")
NEVER:
  - never invent a mux flag: verify on a live box (herdr --help, tmux list-commands, throwaway `tmux -L probe`) or mark UNVERIFIED-* with a fallback
  - never `herdr update` on pacman/mise/brew/nix, never `herdr server stop` / `tmux kill-server` without --force-server-stop, never `herdr machine`, never `pane … --current`
  - never `agent prompt` / send-keys into a blocked agent; unknown status parks only with --force
  - never `omarchy-update` without -y from an agent pane (blocks on a confirm dialog)
  - never implement live transports / live collect / exec against a real mux before the human says "implement MVP next"
  - never let the reviewer touch source; it writes one file under docs/reviews/
