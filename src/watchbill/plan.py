"""Typed ``Plan`` / ``Step`` objects shared by every planner and by ``exec``.

A planner returns a ``Plan``. ``exec`` runs it. The two agree on one thing:
a ``Step`` whose ``mutating`` flag is true is never executed unless the plan
was approved with ``--yes``. ``Plan.scheduled()`` is the list ``exec`` will
actually run; in dry-run it contains no mutating Herdr verb.

Mutating vs read-only is decided from the Herdr argv by ``is_mutating`` so a
planner cannot accidentally schedule ``session stop`` as read-only.
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Sequence


class StepKind(str, Enum):
    HERDR = "herdr"        # argv is a herdr CLI invocation on `host` (transport wraps it)
    SHELL = "shell"        # argv is a plain remote command on `host` (action commands)
    LOCAL = "local"        # argv runs on the cockpit (copy session.json aside, attach viewport)
    SNAP = "snap"          # write a roster (reason in `description`)
    GUARD = "guard"        # occupant-count guard evaluation
    WAIT = "wait"          # herdr agent wait / pane wait; read-only
    JOURNAL = "journal"    # journal checkpoint (host marked set-complete, etc.)
    NOTE = "note"          # human-readable marker in dry-run output
    MANUAL = "manual"      # operator does something by hand; exec waits for confirmation (cmux relaunch)
    CHECK = "check"        # read-only verification against a recorded expectation (agent config drift)


# (group, sub) pairs. A leading "herdr", "--session <S>" and other global
# options are stripped before matching. Single-element tuples match a
# top-level command.
MUTATING_HERDR_VERBS: frozenset[tuple[str, ...]] = frozenset({
    ("session", "stop"), ("session", "delete"),
    ("server", "stop"), ("server", "reload-config"),
    ("update",),
    ("pane", "close"), ("pane", "run"), ("pane", "send-keys"), ("pane", "send-text"),
    ("pane", "split"), ("pane", "move"), ("pane", "swap"), ("pane", "rename"),
    ("pane", "resize"), ("pane", "zoom"), ("pane", "input"),
    ("agent", "start"), ("agent", "prompt"), ("agent", "send-keys"), ("agent", "rename"),
    ("workspace", "create"), ("workspace", "close"), ("workspace", "rename"), ("workspace", "move"),
    ("tab", "create"), ("tab", "close"), ("tab", "rename"), ("tab", "move"),
    ("plugin", "install"), ("plugin", "uninstall"), ("plugin", "link"), ("plugin", "unlink"),
    ("plugin", "enable"), ("plugin", "disable"), ("plugin", "action", "invoke"),
    ("integration", "install"), ("integration", "uninstall"),
    ("api", "call"),  # any raw socket call is treated as mutating (layout.apply etc.)
})

READONLY_HERDR_VERBS: frozenset[tuple[str, ...]] = frozenset({
    ("status",), ("status", "server"), ("status", "client"),
    ("agent", "list"), ("agent", "get"), ("agent", "read"), ("agent", "wait"), ("agent", "explain"),
    ("pane", "list"), ("pane", "get"), ("pane", "current"), ("pane", "layout"),
    ("pane", "process-info"), ("pane", "read"), ("pane", "wait-output"), ("pane", "edges"),
    ("workspace", "list"), ("workspace", "get"), ("tab", "list"), ("tab", "get"),
    ("session", "list"), ("api", "snapshot"), ("api", "schema"),
    ("plugin", "list"), ("plugin", "action", "list"), ("plugin", "status"), ("plugin", "log"),
    ("integration", "status"), ("server", "agent-manifests"),
})

# Verbs Watchbill must never emit outside an explicit operator override.
FORBIDDEN_WITHOUT_FLAG: dict[tuple[str, ...], str] = {
    ("server", "stop"): "--force-server-stop",
    ("update",): "official-installer flavor only (never on pacman/mise/brew/nix)",
}

_GLOBAL_OPTS_WITH_VALUE = {"--session", "--remote", "--remote-keybindings"}


def herdr_verb(argv: Sequence[str]) -> tuple[str, ...]:
    """Return the (group, sub[, sub2]) tuple of a herdr argv, ignoring globals.

    ``["ssh", ..., "--", "herdr", "--session", "s", "pane", "close", "w1:p1"]``
    → ``("pane", "close")``. Non-herdr argv → ``()``.
    """
    toks: list[str] = []
    for t in argv:
        # ssh-rendered steps carry the remote command as one shell-quoted token
        if " " in t and (t.startswith("herdr ") or "/herdr " in t):
            toks.extend(shlex.split(t))
        else:
            toks.append(t)
    if "herdr" in toks:
        toks = toks[toks.index("herdr") + 1:]
    elif toks and toks[0].endswith("/herdr"):
        toks = toks[1:]
    else:
        return ()
    out: list[str] = []
    i = 0
    while i < len(toks) and len(out) < 3:
        t = toks[i]
        if t in _GLOBAL_OPTS_WITH_VALUE:
            i += 2
            continue
        if t.startswith("-"):
            i += 1
            continue
        out.append(t)
        i += 1
    for n in (3, 2, 1):
        if tuple(out[:n]) in MUTATING_HERDR_VERBS or tuple(out[:n]) in READONLY_HERDR_VERBS:
            return tuple(out[:n])
    return tuple(out[:2])


def is_mutating(argv: Sequence[str]) -> bool:
    """True when the argv is a Herdr verb that changes server state.

    Unknown herdr verbs are treated as mutating (fail closed). Non-herdr argv
    is the caller's responsibility (``Step.mutating`` is set explicitly for
    ``SHELL`` steps).
    """
    verb = herdr_verb(argv)
    if not verb:
        return False
    for n in (3, 2, 1):
        if verb[:n] in READONLY_HERDR_VERBS:
            return False
        if verb[:n] in MUTATING_HERDR_VERBS:
            return True
    return True


@dataclass(frozen=True)
class Step:
    id: str
    kind: StepKind
    host: str
    description: str
    argv: tuple[str, ...] = ()
    session: str | None = None
    mutating: bool = False
    slot_id: str | None = None
    human_id: str | None = None
    precondition: str | None = None
    # argv may contain "{pane:<slot_id>}" tokens that exec resolves after the
    # shape has been rebuilt (layout.apply / pane split create *new* ids).
    placeholders: bool = False
    # raw: the argv handed to the transport primitive (herdr args after
    # `herdr --session S`, or the plain shell argv). `argv` is the rendered
    # full command for humans (ssh prefix included).
    raw: tuple[str, ...] = ()
    # creates: slot_id (or "viewport:<host>/<session>") whose pane id this
    # step's JSON result yields; exec fills the placeholder map from it.
    creates: str | None = None
    unverified: bool = False
    # via: which transport primitive runs `raw`: mux | shell | local | none
    via: str = "none"
    mux: str = "herdr"     # backend whose CLI `raw` addresses when via == mux
    # planned_stop: this step IS the blast radius's declared session stop. On
    # tmux the session *is* the server, so `kill-server` is both the ordinary
    # stop and the forbidden one; the flag says which this is.
    planned_stop: bool = False
    # agent_kind: the occupant's kind, so exec can match the right approval
    # patterns when a mux has no native blocked state (tmux).
    agent_kind: str | None = None
    # expect: what a CHECK step compares the live probe against (the occupant's
    # recorded on-disk agent configuration).
    expect: dict | None = None
    # reuse: for a create step (workspace/tab/split), where the pane would sit
    # in the recorded layout — {"workspace": label, "tab": label, "index": n}.
    # A mux that restores its own layout on restart (herdr does, from
    # session.json) may already have that pane; exec takes it instead of
    # creating a duplicate.
    reuse: dict | None = None
    # phase "restore": a per-occupant step after the session is back up
    # (config check, agent start, wait, prompt, relaunch). Its failure costs
    # only that occupant, not every later occupant on the host.
    phase: str = ""

    @property
    def verb(self) -> tuple[str, ...]:
        """Herdr verb tuple for herdr-mux steps; for tmux/cmux the first
        token (``send-keys``, ``kill-server``) as a one-tuple."""
        if self.via in ("herdr", "mux") and self.raw:
            if self.mux == "herdr":
                return herdr_verb(("herdr", *self.raw))
            return (self.raw[0],)
        return herdr_verb(self.argv) if self.kind in (StepKind.HERDR, StepKind.WAIT) else ()


@dataclass(frozen=True)
class Refusal:
    reason: str
    host: str | None = None
    human_id: str | None = None
    slot_id: str | None = None
    override: str | None = None  # the flag that would allow it, if any


@dataclass
class Plan:
    verb: str
    fleet: str
    mode: str = ""
    steps: list[Step] = field(default_factory=list)
    refusals: list[Refusal] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    approved: bool = False  # --yes

    def add(self, step: Step) -> Step:
        self.steps.append(step)
        return step

    def mutating_steps(self) -> list[Step]:
        return [s for s in self.steps if s.mutating]

    def scheduled(self) -> list[Step]:
        """Steps ``exec`` will run.

        Dry-run schedules read-only steps only — and not WAIT steps: a wait
        observes the effect of a mutation that dry-run skipped, so running it
        would block for the full timeout waiting for something nobody asked
        for (an agent told to `/exit` only in the plan text, say).
        """
        if self.approved:
            return list(self.steps)
        return [s for s in self.steps if not s.mutating and s.kind is not StepKind.WAIT]

    def hosts(self) -> list[str]:
        seen: dict[str, None] = {}
        for s in self.steps:
            seen.setdefault(s.host, None)
        return list(seen)

    @property
    def refused(self) -> bool:
        return bool(self.refusals)

    def herdr_verbs(self) -> list[tuple[str, ...]]:
        return [s.verb for s in self.steps if s.verb]

    def to_dict(self) -> dict:
        return {
            "verb": self.verb,
            "fleet": self.fleet,
            "mode": self.mode,
            "approved": self.approved,
            "steps": [
                {
                    "id": s.id, "kind": s.kind.value, "host": s.host, "session": s.session,
                    "description": s.description, "argv": list(s.argv), "mutating": s.mutating,
                    "slot_id": s.slot_id, "human_id": s.human_id, "precondition": s.precondition,
                }
                for s in self.steps
            ],
            "refusals": [r.__dict__ for r in self.refusals],
            "notes": list(self.notes),
        }

    def render(self) -> str:
        head = f"plan: {self.verb} {self.mode}".rstrip() + f"  fleet={self.fleet}  " \
            + ("APPROVED (--yes)" if self.approved else "DRY-RUN (add --yes to execute)")
        lines = [head]
        for r in self.refusals:
            where = r.human_id or r.host or "-"
            hint = f"  [{r.override}]" if r.override else ""
            lines.append(f"  REFUSE {where}: {r.reason}{hint}")
        for s in self.steps:
            flag = "M" if s.mutating else "r"
            who = s.human_id or s.host
            argv = " ".join(s.argv)
            pre = f"  (if {s.precondition})" if s.precondition else ""
            label = s.mux if s.kind is StepKind.HERDR else s.kind.value
            lines.append(f"  [{flag}] {s.id:<6} {label:<7} {who}: {s.description}{pre}")
            if argv and s.kind is StepKind.CHECK:
                lines.append("          $ (read-only config probe: settings, plugins, hooks, MCP, trust, version, integration)")
            elif argv:
                lines.append(f"          $ {argv}")
        for n in self.notes:
            lines.append(f"  note: {n}")
        return "\n".join(lines)


def check_verbs_allowed(steps: Iterable[Step], *, force_server_stop: bool = False) -> list[str]:
    """Return violations of the never-emit list. Used by tests and by exec."""
    bad: list[str] = []
    for s in steps:
        v = s.verb
        if v == ("server", "stop") and not force_server_stop:
            bad.append(f"{s.id}: herdr server stop requires --force-server-stop")
        if s.mux == "tmux" and v == ("kill-server",) and not (force_server_stop or s.planned_stop):
            bad.append(f"{s.id}: unplanned tmux kill-server requires --force-server-stop")
        if v and v[0] == "machine":
            bad.append(f"{s.id}: herdr machine is not a 0.8.2 dependency")
    return bad
