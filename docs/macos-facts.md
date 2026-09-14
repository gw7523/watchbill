# macOS facts Watchbill relies on

Recorded 2026-09-13 on the Mac mini (macOS 26.4.1, arm64) over Tailscale,
using throwaway herdr and tmux servers with haiku Claude test agents in /tmp.
The Mac's own launchd-managed herdr `default` and its running agents were
never touched.

| Fact | Consequence in Watchbill |
|---|---|
| no `setsid` | the default herdr start's `nohup` branch was used and works (server up, detached) |
| Homebrew herdr is **0.9.0, protocol 22**; every CLI verb Watchbill uses has the same usage as 0.8.2, status adds `endpoint_compatible` | roll, park, stop, start, reuse, resume and prompt all ran unchanged against 0.9 |
| an agent in a server started **over SSH** cannot read Claude's credentials from the login keychain ("Not logged in"), although the keychain item is visible from SSH | on macOS start the server in the GUI launchd domain: `start = "launchctl kickstart gui/$(id -u)/<label>"`; agents there are logged in |
| BSD `ps` has no `--sort`; there is no `/proc` | the tmux process probe uses `ps -t` plus `lsof -d cwd`, and always exits 0 (a failing Linux-only tail had discarded a working process list) |
| Claude runs `caffeinate -i -t 300` in the foreground while working, and its process name is its version (`2.1.270`) | an agent's argv is taken from its own process, not the first foreground process (the helper had cost the resumed agent its flags) |
| a Claude showing `/rc active` (Remote Control with a viewer attached) ignores `/exit` and Ctrl-C; after `/rc` → "Disconnect this session", `/exit` works. `/rc` shown alone (available) exits normally | a park refuses when the screen shows `/rc active`; `mux_options.disconnect_remote_control = true` disconnects first. Refusal and disconnect are unit-tested; the `/rc active` state was seen once and not reproduced on demand |
| no `watch` binary by default; a `while sleep 5; do …; done` loop has only `sleep` as a process | a relaunchable watcher must be a real program (`brew install watch`, `entr`), not a shell loop |
| the environment probe reads `/proc` | on macOS the roster records `env_captured: false` and restores no seat environment |

## Live results

| Target | Runs | Result |
|---|---|---|
| herdr 0.9 throwaway session, launchd GUI start | 4 | same session id and flags every run; codeword recalled 4 times |
| tmux 3.7c throwaway server over SSH | 2 | agent detected via BSD ps, parked, resumed with flags and `--continue`; recall not provable (agent logged out: SSH-started server) |

## cmux (2026-09-14)

Everything cmux-specific is in [cmux-facts.md](cmux-facts.md): socket auth
without a password over ssh, agent hooks as the native status source, the
quit that needs `app.confirmQuit = "never"`, and the relaunch (`open -a
cmux` from ssh) that restores workspaces and resumes the agents itself.
Verified twice end to end through `watchbill relieve restart-harness`.

## tmux from the desktop session (2026-09-14)

A tmux server started from a GUI terminal on the Mac (the owner's default
server) gives its agents the login keychain: a throwaway Claude in a new
session on that server showed `Claude Max`, and after `watchbill secure park`
plus `watchbill set` it came back on `claude --model haiku --continue`, still
logged in, and recalled the codeword. The same cycle on a server started over
ssh (`tmux -L wbmac`) resumes a `Not logged in` agent. `hosts.toml` for such a
host: `mux = "tmux"`, `sessions = ["default"]` (the default socket name).

