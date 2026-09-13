"""Agent launch configuration: record it at park, restore it at resume.

An agent is more than its conversation id. The same `claude --resume <id>`
comes back as a *different agent* if its permission mode, model, config
directory (seat), plugins, hooks or MCP servers differ from when it was
parked. This module captures that configuration while the agent is running,
and at resume time either restores it or reports what changed.

Two kinds of configuration, handled differently:

* **Per-process** — flags, working directory, seat-defining environment.
  Watchbill *restores* these exactly: flags go back on the command line, the
  cwd and environment onto the new pane.
* **On disk** — settings, enabled plugins, hooks, MCP servers, folder trust,
  CLI version, Herdr integration. These persist across a restart on their
  own; Watchbill *verifies* them against the record with a read-only CHECK
  step before the agent starts. Expected changes (a window that installed a
  plugin or upgraded a CLI) are reported. Changes that would break the resume
  (folder trust lost → the agent blocks on a dialog; integration gone → Herdr
  can no longer see the agent; config dir gone) stop the host.

Secrets never enter a roster: environment values are recorded only for a
fixed allowlist, a variable whose *name* looks secret is recorded by name only
even if allowlisted, and settings files are fingerprinted, not copied. The
filtering happens on the remote side, in the probe itself.
"""
from __future__ import annotations

import json
import re
import shlex
from typing import Sequence

from . import resume

READONLY_MARK = ": watchbill-readonly;"   # probe bodies start with this

# Environment that defines *which* agent this is (its seat). Restored onto the
# new pane with --env / -e.
RESTORE_ENV = (
    "CLAUDE_CONFIG_DIR", "CODEX_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
    "GH_CONFIG_DIR", "GIT_CONFIG_GLOBAL", "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL",
    "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "MISE_CONFIG_DIR", "MISE_DATA_DIR",
)
# Recorded for the operator (drift display), never applied: the new pane's
# shell rebuilds these itself.
RECORD_ENV = ("HOME", "PATH", "SHELL", "LANG")
SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSW|CREDENTIAL|AUTH|COOKIE)", re.IGNORECASE)

CONFIG_DIR_ENV = {"claude": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME"}
DEFAULT_CONFIG_DIR = {"claude": "~/.claude", "codex": "~/.codex", "grok": "~/.grok", "gemini": "~/.gemini",
                      "opencode": "~/.config/opencode", "cursor": "~/.cursor", "hermes": "~/.hermes"}
HERDR_INTEGRATIONS = ("pi", "omp", "claude", "codex", "copilot", "devin", "droid", "kimi", "opencode", "kilo",
                      "hermes", "qodercli", "qwen", "cursor", "mastracode", "grok")


# -- probes (argv builders; transports run them) ---------------------------

def environ_probe_argv(pid: int) -> list[str]:
    """Read one process's environment on the host, filtering *there*: values
    only for the allowlist (and never for a secret-looking name), names for
    everything else. Linux /proc; same user as the agent."""
    keep = "|".join(RESTORE_ENV + RECORD_ENV)
    body = (f"{READONLY_MARK} tr '\\0' '\\n' </proc/{int(pid)}/environ 2>/dev/null | "
            "while IFS= read -r l; do n=${l%%=*}; "
            f"case \"$n\" in *KEY*|*TOKEN*|*SECRET*|*PASSW*|*CREDENTIAL*|*AUTH*|*COOKIE*) printf 'N %s\\n' \"$n\";; "
            f"{keep}) printf 'V %s\\n' \"$l\";; *) printf 'N %s\\n' \"$n\";; esac; done")
    return ["sh", "-c", body]


def parse_environ(stdout: str) -> tuple[dict[str, str], list[str]]:
    env: dict[str, str] = {}
    secret: list[str] = []
    for line in stdout.splitlines():
        tag, _, rest = line.partition(" ")
        if tag == "V" and "=" in rest:
            name, _, value = rest.partition("=")
            if SECRET_NAME.search(name):        # belt and braces: never keep a secret-looking value
                secret.append(name)
            else:
                env[name] = value
        elif tag == "N" and rest and SECRET_NAME.search(rest):
            secret.append(rest)
    return env, sorted(set(secret))


_PROBE = r'''
import hashlib, json, os, subprocess, sys
kind, cfg, cwd, mux = sys.argv[1:5]
cfg = os.path.expanduser(cfg)
out = {"config_dir": cfg, "config_dir_exists": os.path.isdir(cfg)}
def sha(p):
    try:
        return hashlib.sha256(open(p, "rb").read()).hexdigest()[:16]
    except OSError:
        return None
def js(p):
    try:
        return json.load(open(p))
    except Exception:
        return None
if kind == "claude":
    s = js(os.path.join(cfg, "settings.json")) or {}
    out["settings_sha"] = sha(os.path.join(cfg, "settings.json"))
    out["local_settings_sha"] = sha(os.path.join(cfg, "settings.local.json"))
    out["project_settings_sha"] = sha(os.path.join(cwd, ".claude", "settings.json"))
    out["plugins"] = sorted(k for k, v in (s.get("enabledPlugins") or {}).items() if v)
    out["hooks"] = {e: sum(len(m.get("hooks", [])) for m in ms) for e, ms in (s.get("hooks") or {}).items()}
    out["permission_default"] = (s.get("permissions") or {}).get("defaultMode")
    out["model_default"] = s.get("model")
    home_cfg = os.path.realpath(os.path.expanduser("~/.claude"))
    cj = os.path.expanduser("~/.claude.json") if os.path.realpath(cfg) == home_cfg else os.path.join(cfg, ".claude.json")
    top = js(cj) or {}
    out["mcp_servers"] = sorted((top.get("mcpServers") or {}).keys())
    proj = (top.get("projects") or {}).get(cwd) or {}
    out["project_mcp_servers"] = sorted((proj.get("mcpServers") or {}).keys())
    out["trusted"] = bool(proj.get("hasTrustDialogAccepted"))
elif kind == "grok":
    p = os.path.join(cfg, "config.toml")
    out["settings_sha"] = sha(p)
    try:
        import tomllib
        t = tomllib.load(open(p, "rb"))
    except Exception:
        t = {}
    out["plugins_disabled"] = sorted((t.get("plugins") or {}).get("disabled") or [])
    out["claude_hooks_compat"] = ((t.get("compat") or {}).get("claude") or {}).get("hooks")
    out["model_default"] = t.get("model")
else:
    out["settings_sha"] = None
exe = {"cursor": "cursor-agent"}.get(kind, kind)
try:
    v = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=20)
    out["version"] = (v.stdout or v.stderr).strip().splitlines()[0] if (v.stdout or v.stderr).strip() else None
except Exception:
    out["version"] = None
out["integration"] = None
if mux == "herdr":
    try:
        r = subprocess.run(["herdr", "integration", "status"], capture_output=True, text=True, timeout=20)
        for line in r.stdout.splitlines():
            if line.startswith(kind + ":"):
                out["integration"] = line.split(":", 1)[1].split("(/")[0].strip()
    except Exception:
        pass
print(json.dumps(out, sort_keys=True))
'''


def config_dir_for(kind: str | None, env: dict[str, str] | None) -> str:
    var = CONFIG_DIR_ENV.get(kind or "")
    if var and env and env.get(var):
        return env[var]
    return DEFAULT_CONFIG_DIR.get(kind or "", "~")


def config_probe_argv(kind: str, config_dir: str, cwd: str, mux: str) -> list[str]:
    """One read-only probe for the on-disk configuration of an agent kind:
    settings fingerprint, plugins, hooks, MCP servers, folder trust, CLI
    version and Herdr integration status. Prints one JSON object."""
    py = shlex.join(["python3", "-c", _PROBE, kind, config_dir, cwd or "~", mux])
    return ["sh", "-c", f"{READONLY_MARK} {py}"]


# -- derive / build ------------------------------------------------------------

def _flag_value(flags: Sequence[str], *names: str) -> str | None:
    for i, t in enumerate(flags):
        name, eq, val = t.partition("=")
        if name in names:
            if eq:
                return val
            if i + 1 < len(flags) and not flags[i + 1].startswith("-"):
                return flags[i + 1]
    return None


def permission_mode(kind: str | None, flags: Sequence[str], disk: dict | None) -> str:
    if kind == "claude" and "--dangerously-skip-permissions" in flags:
        return "bypassPermissions"
    mode = _flag_value(flags, "--permission-mode")
    if mode:
        return mode
    return (disk or {}).get("permission_default") or "default"


def model(kind: str | None, flags: Sequence[str], disk: dict | None) -> str | None:
    return _flag_value(flags, "--model", "-m") or (disk or {}).get("model_default")


def build(kind: str | None, argv: Sequence[str], env: dict[str, str] | None, secret_env: Sequence[str] | None,
          disk: dict | None) -> dict:
    """The record stored on the occupant as ``agent_config``."""
    flags = resume.carried_flags(kind, argv)
    env = env or {}
    return {
        "flags": flags,
        "flags_carried": kind in resume.VALUE_FLAGS,
        "permission_mode": permission_mode(kind, flags, disk),
        "model": model(kind, flags, disk),
        "config_dir": config_dir_for(kind, env),
        "env": {k: v for k, v in sorted(env.items()) if k in RESTORE_ENV or k in RECORD_ENV},
        "secret_env": sorted(set(secret_env or ())),
        "disk": dict(disk or {}),
    }


def restore_env(agent_config: dict | None) -> dict[str, str]:
    """The subset of the record that goes back onto the new pane."""
    return {k: v for k, v in ((agent_config or {}).get("env") or {}).items() if k in RESTORE_ENV}


# -- verify ----------------------------------------------------------------------

def diff(recorded_disk: dict | None, live: dict | None) -> tuple[list[str], list[str]]:
    """(notes, hard). Notes are changes worth telling the operator about; hard
    are changes that would make the resumed agent wrong or stuck."""
    rec, cur = recorded_disk or {}, live or {}
    notes: list[str] = []
    hard: list[str] = []
    if not cur:
        return ["config probe returned nothing; configuration not verified"], []
    if rec.get("config_dir_exists") and not cur.get("config_dir_exists"):
        hard.append(f"config dir {cur.get('config_dir')} no longer exists")
    if rec.get("trusted") and cur.get("trusted") is False:
        hard.append("folder trust was revoked: the agent would stop on the trust dialog")
    if rec.get("integration") and rec.get("integration", "").startswith("current") and \
            not (cur.get("integration") or "").startswith("current"):
        hard.append(f"herdr integration {rec.get('integration')!r} → {cur.get('integration')!r}: Herdr would not see the agent")
    if rec.get("version") != cur.get("version") and cur.get("version"):
        notes.append(f"version {rec.get('version')} → {cur.get('version')}")
    for key, label in (("settings_sha", "settings"), ("local_settings_sha", "local settings"),
                       ("project_settings_sha", "project settings")):
        if rec.get(key) != cur.get(key):
            notes.append(f"{label} changed since park")
    for key, label in (("plugins", "plugins"), ("mcp_servers", "MCP servers"), ("project_mcp_servers", "project MCP servers"),
                       ("plugins_disabled", "disabled plugins")):
        a, b = set(rec.get(key) or []), set(cur.get(key) or [])
        if a != b:
            notes.append(f"{label}: " + " ".join([f"+{x}" for x in sorted(b - a)] + [f"-{x}" for x in sorted(a - b)]))
    if (rec.get("hooks") or {}) != (cur.get("hooks") or {}):
        notes.append(f"hooks {rec.get('hooks')} → {cur.get('hooks')}")
    if rec.get("claude_hooks_compat") != cur.get("claude_hooks_compat"):
        notes.append(f"grok claude-hooks compat {rec.get('claude_hooks_compat')} → {cur.get('claude_hooks_compat')}")
    return notes, hard
