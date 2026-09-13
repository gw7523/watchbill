#!/usr/bin/env bash
# Watchbill on tmux, end to end, with throwaway agents in /tmp.
#
#   examples/tmux/demo.sh setup      start tmux -L wbdemo: a haiku Claude and a Grok side by side, a watcher window
#   examples/tmux/demo.sh cycle      park -> kill-server -> restart -> rebuild layout -> resume -> prompt
#   examples/tmux/demo.sh proof      show what came back
#   examples/tmux/demo.sh teardown   kill the demo server and its Watchbill state
#
# Costs a few cents of haiku and one short Grok exchange. The agents run in
# /tmp folders; their transcripts are kept by Claude and Grok themselves
# (~/.claude/projects/-tmp-wb-tmux-a, ~/.grok/sessions/%2Ftmp%2Fwb-tmux-b) and
# age out with each tool's retention. Claude asks to trust a new folder once;
# setup accepts it for the /tmp demo folder only.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
export WATCHBILL_HOME="${WATCHBILL_HOME:-/tmp/watchbill-tmux-demo}"
T=(tmux -L wbdemo)
A=/tmp/wb-tmux-a B=/tmp/wb-tmux-b
wb() { (cd "$repo" && uv run watchbill "$@"); }
# The first tmux command starts the server, and every pane inherits its
# environment; start it without any agent-session identity from the caller.
clean_env() { env -u CLAUDECODE -u CLAUDE_CODE_SESSION_ID -u CLAUDE_CODE_CHILD_SESSION -u CLAUDE_CODE_ENTRYPOINT \
                  -u GROK_CC_SESSION_ID -u TMUX -u TMUX_PANE "$@"; }
say() { printf '\n== %s\n' "$*"; }

setup() {
  mkdir -p "$WATCHBILL_HOME/config" "$A" "$B"
  cp "$here/hosts.toml" "$here/allowlist.txt" "$here/pins.toml" "$WATCHBILL_HOME/config/"
  say "tmux server wbdemo: window 'agents' (claude | grok), window 'watch'"
  # pane ids come from tmux itself: base-index / pane-base-index vary by config
  c=$(clean_env tmux -L wbdemo new-session -d -s demo -n agents -c "$A" -x 220 -y 50 -P -F '#{pane_id}')
  g=$("${T[@]}" split-window -h -t "$c" -c "$B" -P -F '#{pane_id}')
  w=$("${T[@]}" new-window -d -t demo -n watch -c "$A" -P -F '#{pane_id}')
  sleep 1
  keys() { "${T[@]}" send-keys -t "$1" -l "$2"; "${T[@]}" send-keys -t "$1" Enter; }
  keys "$w" 'watch -n 5 date'
  keys "$c" 'claude --model haiku --dangerously-skip-permissions'
  keys "$g" 'grok --permission-mode=bypassPermissions'
  sleep 6
  if "${T[@]}" capture-pane -p -t "$c" | grep -q 'trust this folder'; then
    say "accepting Claude's trust prompt for $A"
    "${T[@]}" send-keys -t "$c" Down; sleep 0.5; "${T[@]}" send-keys -t "$c" Enter; sleep 5
  fi
  say "codewords"
  keys "$c" 'Remember this codeword for later: TMUX-CLAUDE-31. Reply with only: ok'
  keys "$g" 'Remember this codeword for later: TMUX-GROK-47. Reply with only: ok'
  sleep 25
  wb roll --explain
  wb snap -m manual
}

cycle() {
  say "dry-run"; wb relieve restart-harness --host tmux-demo | grep -E 'REFUSE|park|kill-server|start|select-layout|--continue|respawn' || true
  say "execute"; wb relieve restart-harness --host tmux-demo --yes | grep -E '^(FAIL|ran=|  REFUSE|  prompt|  config)' || true
}

proof() {
  sleep 20
  say "layout";   "${T[@]}" list-windows -F '#{window_name} #{window_panes} panes #{window_layout}'
  say "processes"; for p in $("${T[@]}" list-panes -a -F '#{pane_id}'); do
      tty=$("${T[@]}" display-message -p -t "$p" '#{pane_tty}')
      printf '%-16s ' "$p"; ps -t "${tty#/dev/}" -o stat=,args= | awk '$1 ~ /\+/ {sub(/^[^ ]+ /,""); print; exit}'
    done
  # the codeword also appears in the redrawn history (the original instruction), so show
  # what follows the resume prompt, not just any occurrence
  say "answers after the resume prompt"
  for p in $("${T[@]}" list-panes -t demo:agents -F '#{pane_id}'); do
    echo "-- $p"; "${T[@]}" capture-pane -p -t "$p" -S -300 | grep -v '^[[:space:]]*$' | grep -A3 'What codeword' | tail -4
  done
}

teardown() {
  "${T[@]}" kill-server 2>/dev/null || true; rm -rf "$WATCHBILL_HOME"
  echo "demo removed. Agent transcripts remain until Claude/Grok retention clears them; to remove now:"
  echo "  rm -rf ~/.claude/projects/-tmp-wb-tmux-a ~/.grok/sessions/%2Ftmp%2Fwb-tmux-b"
}

"${1:-setup}"
