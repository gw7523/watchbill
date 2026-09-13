# Agent CLI facts Watchbill relies on

Recorded 2026-09-13. Flag tables are read from each CLI's `--help`; live
results come from throwaway agents. Codex and OpenCode had no accounts, so
they ran against a local model (`qwen2.5:1.5b`) in a throwaway Ollama
container bound to localhost, since removed.

| Kind | Version | Park | Resume Watchbill emits | Live result |
|---|---|---|---|---|
| claude | 2.1.267 / 2.1.252 / 2.1.270 | `/exit` | `claude <flags> --resume <id>` | green on rig2, ser6 (herdr) and the Mac mini (herdr 0.9, tmux) |
| grok | 1.0.25 / 1.0.30 | `/exit` | `grok <flags> --resume <id>` | green on rig2 (tmux) and ser6 (herdr) |
| codex | 0.153.4 | `/quit` | `codex resume <flags> <id>` (flags after the subcommand, which takes the same options) | green 2 runs: same session id and flags; the stored rollout gained each post-resume turn |
| opencode | 1.18.29 | `/exit` | `opencode <flags> --session <id>` | green 2 runs: same session id and flags; the exported session gained each post-resume turn |
| gemini | 0.59.0 | not verified (no account) | `gemini <flags> --resume <id>` | flag table only |
| cursor | 2026.09.10 | not verified (no account) | `cursor-agent <flags> --resume <id>` | flag table only |

Found live:

| Fact | Consequence in Watchbill |
|---|---|
| a small local model answers recall questions wrongly even when the conversation resumed | continuity is proven from the agent's own session storage (codex `~/.codex/sessions/**/rollout-*-<id>.jsonl`, `opencode export <id>`), not from recall alone |
| codex's first Enter after `/quit` can land while its slash popup opens and only complete the command | exit waits press Enter once more after 4 s if the agent is still there |
| codex asks to trust a new directory, then to trust the hooks it found; herdr reports the agent `idle` during both dialogs | choosing "continue without trusting" disables the herdr integration hook (no session id); the dialogs must be answered before an agent is catalogued as idle |
| opencode takes its provider from `OPENCODE_CONFIG`; a pane restored by herdr has the new server's environment, not the agent's | seat variables are exported into the pane's shell before the agent starts; `OPENCODE_CONFIG_CONTENT` is never recorded (inline provider config can hold API keys) |
| cursor-agent accepts `--api-key` and `-H/--header` on the command line | credential flags are redacted in every recorded command line and never carried into a resume |
