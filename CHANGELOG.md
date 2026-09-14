# Changelog

## 0.1.0 — 2026-09-14 (initial release)

First release that has run every mutating verb against real agents.

- **Muxes:** Herdr 0.8.2 and 0.9.0 (Linux and macOS), tmux 3.x (Linux and
  macOS), cmux 0.64 (macOS). One `mux` per host in `hosts.toml`.
- **Agents parked and resumed live:** Claude Code, Grok, Codex, OpenCode.
  Gemini CLI and Cursor have flag tables only (no account to test with).
- **Verbs:** `roll`, `snap`, `secure`, `set`, `relieve` (`overhaul`), `status`,
  `doctor`. Every mutating verb is a dry-run until `--yes`.
- **Maintenance actions:** `upgrade-mux`, `upgrade-agents`, `install-plugin`,
  `reload-config`, `restart-harness`, `restart-herdr`, `omarchy-update`,
  `custom`, each with a declared blast radius; rolling across hosts with
  `--resume` after a failed host.
- **Same agent comes back:** flags, permission mode, model, config directory
  and non-secret seat environment are recorded at park and restored at
  resume; the on-disk config is fingerprinted and verified before the agent
  starts. Credential flags are redacted and secret values are never stored.
- **Same environment comes back:** a mux server restarted over SSH gets the
  user's desktop session environment, not the SSH connection.
- **Transports:** local, `ssh_cli` (with per-host `ssh_options`), and an
  `exec_prefix` to manage a seat inside a container (distrobox) on any host.
- **Guards:** exclude / ignore globs, occupant-count guard on `current.json`,
  never an unplanned server stop, never a key typed into a blocked agent,
  Claude Remote Control detected before a park.
- **`set` after `secure park`** falls in from the pre-park roster by default
  (the post-park roster lists the parked agents as shells).
- **cmux:** native agent detection, status and session ids from cmux's agent
  hooks; scripted quit and relaunch; the app's own resume is waited for.
