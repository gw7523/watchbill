# tmux facts Watchbill relies on

Recorded 2026-09-11 on `tmux 3.7c` (Arch) using an isolated throwaway server
(`tmux -L wbprobe …`, killed afterwards). Everything below was read from
`tmux list-commands` or observed output. tmux is the multiplexer on the
owner's Macs, so this backend is second only to Herdr.

## Server / session / window / pane model

| tmux | Watchbill roster field |
|---|---|
| server socket (`-L <name>`, default `default`) | `session` (the "herdr session" slot of `human_id`) |
| session (`#{session_name}`) | `workspace_label` |
| window (`#{window_index}` / `#{window_name}`) | `tab_label` (name, falling back to index) |
| pane (`#{pane_id}` = `%N`, `#{pane_index}`) | `pane_label` (`p<index>`), `live_ids.pane_id` = `%N` |

`%N` pane ids and `@N` window ids are unique for the life of the server and
never reused — the same generation-scoping rule as Herdr.

## Read-only collection (verified)

These are the exact formats `src/watchbill/mux/tmux.py` sends: they add
`session_id`, `window_id` and `pane_tty` so the parser keys on tmux's own
stable ids rather than on names. `tests/fixtures/tmux-3.7c.txt` is a capture
in these formats.

```
tmux -L S list-sessions -F '#{session_name}|#{session_id}|#{session_windows}|#{session_attached}|#{session_created}'
tmux -L S list-windows -a -F '#{session_name}|#{window_index}|#{window_id}|#{window_name}|#{window_layout}|#{window_panes}'
tmux -L S list-panes  -a -F '#{session_name}|#{session_id}|#{window_index}|#{window_id}|#{window_name}|#{pane_index}|#{pane_id}|#{pane_pid}|#{pane_current_command}|#{pane_current_path}|#{pane_title}|#{pane_active}|#{window_activity}|#{pane_width}x#{pane_height}|#{pane_left},#{pane_top}|#{pane_tty}'
tmux -L S display-message -p '#{version}|#{socket_path}|#{pid}'
tmux -L S display-message -t %N -p '#{pane_tty}'
ps -t <pts/N> -o pid,ppid,stat,args          # foreground = STAT contains '+'
tmux -L S capture-pane -p -t %N -S -40       # excerpt (last 40 lines)
```

Observed sample (alpha has two windows, the first split in two):

```
alpha|1|edit|1|%0|3345069|sleep|/tmp|RIG2|0|1789159586|40x24|0,0
alpha|1|edit|2|%1|3345072|sleep|/tmp|RIG2|1|1789159586|39x24|41,0
alpha|2|logs|1|%2|3345075|sleep|/tmp|RIG2|1|1789159586|80x24|0,0
beta|1|tmp|1|%3|3345078|bash|/tmp|holloway@RIG2:/tmp|1|1789159586|80x24|0,0
alpha|1|edit|8205,80x24,0,0{40x24,0,0,0,39x24,41,0,1}|2      ← window_layout
```

`#{window_layout}` is a complete, re-applicable description of a window's
splits: `select-layout -t <session>:<window> '<layout>'` recreates it (the
pane ids inside are advisory; tmux maps by order). This is the one thing
tmux does better than Herdr 0.8.2 for `set`.

`#{pane_current_command}` is the foreground process name (`claude`, `sleep`,
`bash`). There is **no agent detection, no agent session id, and no
idle/working/blocked state**: Watchbill derives those (see
[mux-backends.md](mux-backends.md), "Agent identity without a mux that knows agents").

## Mutation (verified command surface)

```
tmux -L S new-session  -d -s <label> -c <cwd> -n <win> -P -F '#{session_id}|#{pane_id}'   # workspace create; -n names
                                                                       # the first window so select-layout can target it
tmux -L S new-window   -d -t <session> -n <name> -c <cwd> -P -F '#{pane_id}'   # tab create
tmux -L S split-window -d -t %N -h|-v -c <cwd> -P -F '#{pane_id}'      # pane split (right | down)
tmux -L S select-layout -t <session>:<window> '<layout string>'
tmux -L S send-keys -t %N -l '<literal text>'                          # -l: no key-name parsing
tmux -L S send-keys -t %N Enter
tmux -L S send-keys -t %N C-c
tmux -L S respawn-pane -k -t %N -c <cwd> <command…>                    # relaunch a watcher in place
tmux -L S kill-pane -t %N | kill-window -t <s>:<w> | kill-session -t <s>
tmux -L S kill-server                                                  # = "server stop"; never default
tmux -L S source-file ~/.tmux.conf                                     # live config reload, PTYs kept
```

`-P -F '#{pane_id}'` on `new-session` / `new-window` / `split-window`
prints the created pane id, which fills Watchbill's `{pane:<slot>}`
placeholders exactly like Herdr's JSON results.

## What tmux cannot do

- No "agent start" with readiness: Watchbill sends the resume argv as text
  and waits for `#{pane_current_command}` to become the agent binary, then
  for output to go quiet.
- No `blocked` signal: approval prompts are detected heuristically from
  `capture-pane` text (patterns per agent kind) and otherwise reported as
  `unknown`, which `secure` refuses to park without `--force`.
- No native session id: resume uses each agent's cwd-scoped continue form
  (`claude --continue`, `grok --continue`, `codex resume --last`,
  `cursor-agent --continue`, `opencode --continue`, `gemini --resume latest`)
  and refuses when two agents of one kind share a cwd on a host.
- No persistence across `kill-server` (tmux-resurrect is not a dependency).
  Cold restart is: kill-server → new-session … → select-layout → resume.
- Upgrading the tmux binary does not touch a running server; a new client
  may refuse to attach across a protocol bump. `upgrade-mux` on tmux is
  therefore cold (park, kill-server, upgrade, start, set).

## Plugins

TPM (`~/.tmux/plugins/tpm`) is the de-facto plugin manager:
`~/.tmux/plugins/tpm/bin/install_plugins` then `tmux source-file
~/.tmux.conf`. Both are live; no server restart, nothing parked.
UNVERIFIED on the Macs whether TPM is installed; the action probes for the
script and refuses if absent.
