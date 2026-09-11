---
name: watchbill
description: Drive the Watchbill cockpit CLI to catalog (roll/snap), stand down (secure), restore (set), and run maintenance windows (relieve) over a fleet of coding agents running in Herdr, tmux, or cmux. Use when the user asks what agents are running where, to park or restore the fleet, or to upgrade the multiplexer / agent CLIs / plugins without losing the map. Requires the `watchbill` CLI; never calls herdr or tmux directly for fleet operations.
---

# Watchbill for a chief-of-staff agent

You are allowed to *drive* Watchbill. You are not allowed to *be* Watchbill:
do not stop Herdr sessions, close panes, or type into other agents with raw
`herdr` commands when a Watchbill verb exists for it.

## Always

1. `watchbill roll` first. Read the roster before proposing anything.
2. Every mutating verb (`secure`, `set`, `relieve`) is a **dry-run** by
   default. Run it once without `--yes`, show the plan to the human, and add
   `--yes` only after they approve *that* plan.
3. Report exit codes: `3` means Watchbill refused an unsafe plan. Relay the
   `REFUSE` lines verbatim and stop. Do not retry with an override flag on
   your own.

## Never, unless the human said the word

| Flag | Say it only if the human literally asked for it |
|---|---|
| `--force` | "force" — parks agents that are still working |
| `--include-local` | "include local" / "the cockpit too" — touches the machine Watchbill runs on |
| `--allow-reboot` | "reboot" — lets an action reboot a worker host (never the cockpit) |
| `--force-server-stop` | "server stop" — an *unplanned* whole-server stop: `herdr server stop`, or a `tmux kill-server` that is not the declared stop of a maintenance window. A cold tmux window's own `kill-server` is planned and needs no flag. |
| `--mode live` | "live handoff" — and only if `watchbill doctor` says `handoff=supported` |
| `snap --force` | "force the roster" — overrides the occupant-count guard |

If the human's request would need one of these and they did not say the
word, ask, quoting the flag and what it does.

## One mux per host

`hosts.toml` gives each host a `mux`: **herdr** (knows its agents natively),
**tmux** (Watchbill derives role from argv and status from quiet time plus
approval-prompt patterns — an `idle` there is a *guess*, flagged in the
roster as `status: heuristic`), or **cmux** (macOS; only what the published
CLI documents, so quitting and relaunching the app are `MANUAL` steps the
human performs while Watchbill waits).

Tell the human when these bite:

- tmux and cmux have no live handoff, so `relieve --mode live` is refused.
- tmux agents resume with their cwd-scoped `--continue`; when two agents of
  one kind share a directory Watchbill refuses to guess and starts fresh.
- `reload-config` is live on herdr and tmux, refused on cmux.
- A tmux occupant whose status is `unknown` needs `--force` to park, exactly
  like a `working` one.

## Verbs

```
watchbill roll                          # who is where
watchbill snap -m manual                # save the map
watchbill secure park <target>          # stand one down (dry-run)
watchbill secure park <target> --yes    # do it
watchbill set <target>                  # bring back (dry-run) → --yes
watchbill relieve upgrade-agents --kinds claude --host ser6      # dry-run window
watchbill relieve install-plugin --plugin owner/repo --startup-hooks
watchbill relieve omarchy-update --host ser6 --expected-version 0.8.3
watchbill relieve <action> --resume     # continue a window after a failure
watchbill status                        # drift since last roster
watchbill doctor                        # flavor / handoff per host
```

Targets: `slot_id`, `human_id` (`host/session/workspace/tab/pane`), or a
label. If the plan refuses with "matches on hosts", add `--host <name>`.

## Reading a plan

- `[M]` steps mutate; `[r]` steps are read-only. In a dry-run only `[r]`
  steps execute.
- `REFUSE …` lines mean exit 3 unless the bracketed override is given.
- `note:` lines are skips (self pane, bridges, cockpit host, watchers not in
  the allowlist). Mention them to the human; they are not errors.
- A `blocked` agent is never typed into. Tell the human which one needs a
  hand before the window can proceed.

## Do not

- Do not use `herdr machine`, `herdr --remote`, or `herdr update` yourself.
- Do not edit rosters, `slots.json`, or `journal.jsonl` by hand.
- Do not run `omarchy-update` without `-y` from a pane; it blocks on a dialog.
- Do not assume a plugin's startup hooks ran because `plugin enable` succeeded.
