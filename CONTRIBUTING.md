# Contributing to Watchbill

Four rules. Every PR is judged against them before anything else.

1. **Planners are pure.** `plan_secure.py`, `plan_set.py`, `plan_relieve.py`
   take a roster plus already-collected live facts and return a `Plan`. They
   never open a socket, never spawn `herdr`, never touch the filesystem. If a
   planner needs a fact it does not have, add the fact to the collector or the
   probe, not a side effect to the planner.
2. **`exec.py` mutates.** It is the only module allowed to run a mutating
   Herdr verb (`session stop`, `pane close`, `pane run`, `agent start`,
   `agent prompt`, `workspace create|close`, `layout.apply`, `plugin install`).
   Everything else is read-only. Dry-run is the default; `--yes` is the switch.
3. **Transports isolate SSH.** `transport/` is the only place that knows
   whether a host is local, reached by `ssh … -- herdr --session S`, by socket
   forward, or by `herdr --remote`. Collectors, planners and actions speak
   `HostSession`, never `subprocess.run(["ssh", …])`.
4. **Actions declare blast radius.** A maintenance action returns a
   `BlastRadius` (which roles to park, whether the session must stop, whether
   a client must be attached, whether a reboot is permitted) and a list of
   `RemoteCmd`. It never stops a session itself. `plan_relieve` composes the
   window; `exec` applies it.

Plus the identity rule from the contract: **never key on `w1:p2`**. Plans key
on `slot_id` (ULID) or `human_id` (`host/session/workspace/tab/pane`). Live
pane ids are hints for one server generation and are rewritten after `set`.

## Practical

- Python 3.11+, stdlib first. `uv sync --group dev`, `uv run pytest`.
- Tests never need a live Herdr. Fixtures are recorded `api snapshot` and
  `pane process-info` JSON under `tests/fixtures/`.
- If you must guess a Herdr 0.8.2 flag, mark it `UNVERIFIED-0.8.2` in
  `docs/architecture.md` and keep a fallback path. Check
  `docs/herdr-0.8.2-facts.md` first; it records what was verified on a live box.
- Small commits: schema, planners, cli stubs, tests.
- Do not vendor other Herdr plugins. Do not depend on `herdr-resurrect`,
  `herdr-hub`, `herdr-suspend-workspace`, or `herdr-muster`.
