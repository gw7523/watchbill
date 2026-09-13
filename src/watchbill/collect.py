"""Collect: raw mux facts → :class:`Roster`. Read-only.

Two halves:

* :func:`gather` pulls facts through a :class:`HostSession` using only the
  backend's read-only verbs (status, listing, per-pane process info, screen
  excerpt, cmux resume binding). Herdr: never ``pane layout --current``
  (#2297) and never a ``layout.export`` ``command`` field (pitfall 3): argv
  comes from process info for every pane, on every mux that has it.
* :func:`build_roster` is pure: facts + slot store + allowlist + pins →
  roster. Tests feed it recorded fixtures (herdr ``api snapshot`` dicts or
  backend-neutral :class:`MuxSnapshot` objects).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import classify as _classify
from . import detect, doctor, prompts, resume
from . import mux as _mux
from .hosts import Host
from .mux.base import MuxSnapshot
from .mux.herdr import snapshot_from_herdr
from .roster import HostRecord, LiveIds, Occupant, Roster, Shape, now_iso
from .slots import SlotStore
from .transport import make_session
from .transport.base import HostSession


@dataclass
class SessionFacts:
    name: str
    status: dict | None = None             # backend Status.raw (herdr: status server --json)
    snapshot: dict | MuxSnapshot | None = None   # herdr api snapshot dict, or a MuxSnapshot
    process_info: dict[str, dict] = field(default_factory=dict)  # pane_id → process_info
    excerpts: dict[str, str] = field(default_factory=dict)       # pane_id → text
    bindings: dict[str, dict] = field(default_factory=dict)      # cmux: pane_id → surface resume binding
    error: str | None = None
    running: bool = True
    version: str | None = None


@dataclass
class HostFacts:
    host: str
    reachable: bool = True
    herdr_path: str | None = None
    pacman_owned: bool = False
    cockpit: bool = False
    mux: str = "herdr"
    sessions: list[SessionFacts] = field(default_factory=list)
    error: str | None = None


# -- live gather (transport-backed, read-only) ----------------------------

def gather(hs: HostSession, *, excerpts: bool = False, excerpt_lines: int = 40) -> SessionFacts:
    be = hs.backend
    st = hs.mux(*be.status_argv())
    status = be.parse_status(st.stdout, st.ok)
    if not st.ok and be.name == "herdr":
        return SessionFacts(hs.session, error=st.stderr.strip() or "status failed", running=False)
    facts = SessionFacts(hs.session, status=status.raw or {"running": status.running, "version": status.version},
                         running=status.running, version=status.version)
    if not status.running:
        return facts
    outs = []
    for argv in be.snapshot_argvs():
        r = hs.mux(*argv)
        if not r.ok:
            facts.error = f"{be.name} listing failed: {' '.join(argv)}"
            return facts
        outs.append(r.stdout)
    snap = be.parse_snapshot(outs)
    facts.snapshot = snap
    for pane in snap.panes:
        pi_argv = be.process_info_argv(pane)
        if pi_argv:
            r = hs.shell(pi_argv) if pi_argv[:2] == ["sh", "-c"] else hs.mux(*pi_argv)
            if r.ok:
                facts.process_info[pane.pane_id] = be.parse_process_info(pane, r.stdout)
        want_excerpt = excerpts or be.caps.agent_status == "heuristic"   # tmux needs the screen for status
        ex_argv = be.excerpt_argv(pane.pane_id, excerpt_lines) if want_excerpt else None
        if ex_argv:
            r = hs.mux(*ex_argv)
            if r.ok:
                facts.excerpts[pane.pane_id] = r.stdout
        if be.name == "cmux":
            r = hs.mux(*be.resume_binding_argv(pane.pane_id))
            if r.ok and r.stdout.strip():
                try:
                    import json
                    facts.bindings[pane.pane_id] = json.loads(r.stdout)
                except ValueError:
                    pass
    return facts


def probe_install(hs: HostSession) -> tuple[str | None, bool]:
    """Two read-only shell probes: where the mux binary lives and whether a
    package owns it. Shared by ``gather_host`` and ``doctor.check`` so a plain
    ``roll`` can report the install flavor (pacman hosts never get an
    in-place upgrade command)."""
    mux_bin = hs.backend.name
    path = hs.shell(["sh", "-c", f"command -v {mux_bin}"])
    owner = hs.shell(["sh", "-c", f'pacman -Qo "$(command -v {mux_bin})" >/dev/null 2>&1 && echo yes || echo no'])
    return (path.stdout.strip() or None), owner.stdout.strip() == "yes"


def idle_after_map(fleet) -> dict[str, float]:
    """Per-host quiet threshold from ``[[host]] mux_options.idle_after_s``."""
    return {h.name: float(h.mux_options.get("idle_after_s", 30.0)) for h in fleet.hosts}


def gather_host(host: Host, *, excerpts: bool = False) -> HostFacts:
    hf = HostFacts(host=host.name, cockpit=host.cockpit, mux=host.mux)
    probed = False
    for name in host.sessions:
        hs = make_session(host, name)
        try:
            if not probed:
                hf.herdr_path, hf.pacman_owned = probe_install(hs)
                probed = True
            hf.sessions.append(gather(hs, excerpts=excerpts))
        except Exception as exc:  # transport errors mark the host, never abort the roll
            hf.reachable = False
            hf.error = str(exc)
            break
    return hf


# -- pure build ---------------------------------------------------------

def _dedupe_workspace_labels(snap: MuxSnapshot) -> dict[str, str]:
    """workspace_id → unique label. Two workspaces named ``Work`` in one
    session become ``Work`` and ``Work#5`` (suffix = workspace number)."""
    seen: dict[str, int] = {}
    out: dict[str, str] = {}
    for ws in sorted(snap.workspaces, key=lambda w: w.number or 0):
        seen[ws.label] = seen.get(ws.label, 0) + 1
        out[ws.workspace_id] = ws.label if seen[ws.label] == 1 else f"{ws.label}#{ws.number}"
    return out


def _binding_argv(binding: dict | None) -> tuple[list[str] | None, str | None]:
    """cmux ``surface resume show --json`` → (argv, kind). Field names are
    UNVERIFIED-LIVE; accept ``command``/``argv``/``resume``."""
    if not binding:
        return None, None
    import shlex
    cmd = binding.get("argv") or binding.get("command") or binding.get("resume")
    if isinstance(cmd, str):
        cmd = shlex.split(cmd)
    if not cmd:
        return None, None
    exe = cmd[0].rsplit("/", 1)[-1]
    kind = next((k for k, e in _classify.AGENT_KINDS.items() if e == exe), None)
    return list(cmd), kind


def build_roster(fleet: str, facts: list[HostFacts], *, slots: SlotStore, allowlist: _classify.Allowlist | None = None,
                 pins: prompts.Pins | None = None, templates: dict[str, str] | None = None,
                 cockpit: dict | None = None, reason: str = "manual", now: str | None = None,
                 keep_excerpts: bool = True, idle_after_s: float = 30.0,
                 idle_after: dict[str, float] | None = None) -> Roster:
    now = now or now_iso()
    roster = Roster(fleet=fleet, taken_at=now, reason=reason, cockpit=cockpit or {})
    for hf in facts:
        status0 = next((s.status for s in hf.sessions if s.status), None) or {}
        flavor = doctor.flavor_from(hf.herdr_path, pacman_owned=hf.pacman_owned)
        version = status0.get("version") or next((s.version for s in hf.sessions if s.version), None)
        roster.hosts.append(HostRecord(
            host=hf.host, reachable=hf.reachable, cockpit=hf.cockpit, error=hf.error, mux=hf.mux,
            herdr={"version": version, "protocol": status0.get("protocol"),
                   "flavor": flavor, "socket": status0.get("socket"),
                   "live_handoff": bool((status0.get("capabilities") or {}).get("live_handoff"))},
        ))
        caps = _mux.CAPS[hf.mux]
        host_idle = (idle_after or {}).get(hf.host, idle_after_s)
        for sf in hf.sessions:
            if not sf.snapshot:
                continue
            snap = sf.snapshot if isinstance(sf.snapshot, MuxSnapshot) else snapshot_from_herdr(sf.snapshot)
            ws_labels = _dedupe_workspace_labels(snap)
            shape = Shape(host=hf.host, session=sf.name, mux=hf.mux)
            ws_shapes: dict[str, dict] = {}
            for ws in sorted(snap.workspaces, key=lambda w: w.number or 0):
                ws_shapes[ws.workspace_id] = {"label": ws_labels[ws.workspace_id], "number": ws.number,
                                              "workspace_id": ws.workspace_id, "cwd": None, "tabs": []}
            tab_shapes: dict[str, dict] = {}
            for t in snap.tabs:
                ts = {"label": t.label, "number": t.number, "tab_id": t.tab_id, "panes": [], "splits": t.splits,
                      "layout": t.layout, "zoomed": False}
                tab_shapes[t.tab_id] = ts
                if t.workspace_id in ws_shapes:
                    ws_shapes[t.workspace_id]["tabs"].append(ts)
            # cwd-uniqueness for the continue-form resume (no native id): kind+cwd must be unique per host
            cwd_kind_count: dict[tuple[str, str], int] = {}
            if not caps.native_resume:
                for p in snap.panes:
                    pinfo0 = sf.process_info.get(p.pane_id)
                    cls0 = _classify.classify(_pane_dict(p), pinfo0)
                    if cls0.role == "agent" and cls0.kind:
                        # same key the lookup uses below, or two agents sharing a
                        # real cwd would both still get `--continue`
                        cwd_kind_count[_cwd_key(p, pinfo0)] = cwd_kind_count.get(_cwd_key(p, pinfo0), 0) + 1
            seen_labels: dict[tuple[str, str], int] = {}
            for pane in snap.panes:
                pid = pane.pane_id
                wsid, tid = pane.workspace_id, pane.tab_id
                ws_label = ws_labels.get(wsid, wsid)
                tab = snap.tab(tid)
                tab_label = tab.label if tab else "1"
                pane_label = pane.name or f"p{pane.index}"
                if hf.mux == "cmux":
                    pane_label = pane.name or f"s{pane.index}"
                n = seen_labels[(tid, pane_label)] = seen_labels.get((tid, pane_label), 0) + 1
                if n > 1:   # two agents both named "claude" in one tab must not share a slot
                    pane_label = f"{pane_label}#{pane.index}"
                human_id = f"{hf.host}/{sf.name}/{ws_label}/{tab_label}/{pane_label}"
                pinfo = sf.process_info.get(pid)
                binding_argv, binding_kind = _binding_argv(sf.bindings.get(pid))
                cls = _classify.classify(_pane_dict(pane), pinfo)
                if cls.role == "shell" and binding_kind:   # cmux: the resume binding is the only agent signal
                    cls = _classify.Classification("agent", kind=binding_kind, argv=tuple(binding_argv or ()),
                                                   cmdline=" ".join(binding_argv or ()), reason="cmux resume binding")
                excerpt = sf.excerpts.get(pid)
                # status: native, else heuristic from quiet time + screen, else unknown
                if cls.role == "agent":
                    if caps.agent_status == "native":
                        status = pane.agent_status
                    elif caps.agent_status == "heuristic":
                        status = detect.agent_status(cls.kind, activity_age_s=pane.activity_age_s, screen=excerpt,
                                                     idle_after_s=host_idle)
                    else:
                        status = "unknown"
                else:
                    status = pane.agent_status if caps.agent_status == "native" else None
                sess = pane.agent_session if (cls.role == "agent" and caps.agent_status == "native") else None
                sess_val = sess.get("value") if sess else None
                slot_id = slots.assign(human_id=human_id, host=hf.host, agent_session=sess_val or (binding_argv and " ".join(binding_argv)),
                                       kind=cls.kind, now=now)
                cwd = pane.cwd or ""
                fg_cwd = pane.foreground_cwd or None
                if pinfo and pinfo.get("foreground_processes"):
                    fg_cwd = fg_cwd or pinfo["foreground_processes"][0].get("cwd") or None
                tasking = (pane.title or None) if cls.role == "agent" else (cls.cmdline or None)
                # resume argv: native id → binding → cwd-scoped continue (guarded) → none
                resume_argv: list[str] | None
                resume_note = None
                if cls.role != "agent":
                    resume_argv = None
                elif sess_val:
                    resume_argv = resume.resume_argv(cls.kind, sess_val, cls.argv)
                elif binding_argv:
                    resume_argv = binding_argv
                elif not caps.native_resume and cls.kind:
                    if cwd_kind_count.get((cls.kind, _effective_cwd(pane, pinfo)), 0) > 1:
                        resume_argv = None
                        resume_note = "continue-form ambiguous: another agent of this kind shares the cwd"
                    else:
                        resume_argv = resume.continue_argv(cls.kind, cls.argv)
                else:
                    resume_argv = None
                rp = prompts.resolve(human_id=human_id, slot_id=slot_id, role=cls.role, kind=cls.kind,
                                     cwd=fg_cwd or cwd, tasking=tasking, pins=pins, templates=templates,
                                     excerpt=excerpt if keep_excerpts else None)
                occ = Occupant(
                    slot_id=slot_id, human_id=human_id, host=hf.host, session=sf.name,
                    workspace_label=ws_label, tab_label=tab_label, pane_label=pane_label,
                    role=cls.role, kind=cls.kind, agent_status=status,
                    argv=list(cls.argv), cmdline=cls.cmdline, cwd=cwd, foreground_cwd=fg_cwd,
                    agent_session=sess, resume_argv=resume_argv,
                    resume_prompt=rp, allow_relaunch=_classify.allow_relaunch(cls.role, cls.cmdline, allowlist),
                    live_ids=LiveIds(workspace_id=wsid, tab_id=tid, pane_id=pid, terminal_id=pane.terminal_id),
                    tasking=_tasking(tasking, resume_note,
                                 heuristic=(cls.role == "agent" and caps.agent_status == "heuristic")),
                    excerpt=excerpt if keep_excerpts else None, taken_at=now, mux=hf.mux,
                )
                roster.occupants.append(occ)
                if wsid in ws_shapes and ws_shapes[wsid]["cwd"] is None:
                    ws_shapes[wsid]["cwd"] = occ.effective_cwd
                if tid in tab_shapes:
                    tab_shapes[tid]["panes"].append({"pane_label": pane_label, "slot_id": slot_id, "pane_id": pid,
                                                     "cwd": occ.effective_cwd, "rect": pane.rect})
            shape.workspaces = list(ws_shapes.values())
            roster.shapes.append(shape)
    return roster


def _tasking(tasking: str | None, resume_note: str | None, *, heuristic: bool) -> str | None:
    """Operator-visible notes. A heuristic agent status is flagged: on tmux an
    `idle` is a guess from quiet time, and a false `idle` is the dangerous
    direction (it would let something type into a live dialog)."""
    bits = [b for b in (tasking, resume_note, "status: heuristic" if heuristic else None) if b]
    if not bits:
        return None
    head, rest = bits[0], bits[1:]
    return head + ("".join(f" [{r}]" for r in rest) if rest else "")


def _effective_cwd(pane, pinfo: dict | None) -> str:
    """The cwd an occupant really runs in: the foreground process's, else the
    pane's. Pitfall 4, applied identically wherever cwd identity matters."""
    if pinfo and pinfo.get("foreground_processes"):
        got = pinfo["foreground_processes"][0].get("cwd")
        if got:
            return got
    return pane.foreground_cwd or pane.cwd or ""


def _cwd_key(pane, pinfo: dict | None) -> tuple[str, str]:
    from .classify import classify as _c
    cls = _c(_pane_dict(pane), pinfo)
    return (cls.kind or "", _effective_cwd(pane, pinfo))


def _pane_dict(p) -> dict:
    """MuxPane → the pane dict shape ``classify`` expects."""
    return {"pane_id": p.pane_id, "agent": p.agent, "agent_status": p.agent_status}


def strip_excerpts(roster: Roster) -> Roster:
    """Excerpts are cockpit-local unless ``snap.excerpt_sync``."""
    for o in roster.occupants:
        o.excerpt = None
    return roster
