# Watchbill on tmux

A tmux server knows nothing about coding agents, so Watchbill works them out:

| Question | Herdr answers it | On tmux Watchbill uses |
|---|---|---|
| Is this pane an agent, and which? | its own detection | the pane's foreground process (`ps -t <pane tty>`), including agents behind an interpreter such as `node …/grok` |
| Is it idle? | its own state | the window has been quiet for `idle_after_s` and no approval prompt is on screen; the roster marks it `status: heuristic` |
| Which conversation is it? | a session id | none: resume uses the agent's own cwd-scoped continue (`claude --continue`, `grok --continue`), refused when two agents of one kind share a directory |
| How do I get the layout back? | it restores it | the recorded `window_layout`, re-applied with `select-layout` |

Everything else is the same as on Herdr: each agent comes back with the flags
it was started with, watchers in `allowlist.txt` are relaunched, a pinned
prompt is typed after resume, and the server is started in the user's desktop
session environment rather than the SSH connection Watchbill arrived on.

## Run the demo

```bash
examples/tmux/demo.sh setup      # tmux -L wbdemo: haiku Claude | Grok, and a `watch` window; codewords sent
examples/tmux/demo.sh cycle      # park → kill-server → start → rebuild layout → resume → prompt
examples/tmux/demo.sh proof      # layout, running commands, and whether each agent recalled its codeword
examples/tmux/demo.sh teardown
```

State lives in `WATCHBILL_HOME` (default `/tmp/watchbill-tmux-demo`), so your
real fleet config is never touched. The agents run in `/tmp`.

## Use it for your own tmux

Copy `hosts.toml`, set `sessions` to your tmux socket names (`default` for
plain `tmux`), and drop `idle_after_s` back to the default. For a tmux server
on another machine use `transport = "ssh_cli"` and `target = "<ssh alias>"`.

A cold tmux window really does end every process on that server (`kill-server`
is the only way to stop tmux), which is why Watchbill parks the agents first
and plans the stop as part of the window; an unplanned `kill-server` still
needs `--force-server-stop`.
