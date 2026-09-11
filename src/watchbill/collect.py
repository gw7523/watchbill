"""Collect: raw Herdr facts → :class:`Roster`. Read-only.

Two halves:

* :func:`gather` pulls facts through a :class:`HostSession` using only
  read-only verbs (``status server --json``, ``api snapshot``,
  ``pane process-info --pane <id>``, ``pane read``). It never calls
  ``pane layout --current`` (#2297) and never trusts a ``command`` field from
  ``layout.export`` (pitfall 3): argv comes from ``process-info`` for every
  pane.
* :func:`build_roster` is pure: facts + slot store + allowlist + pins →
  roster. Tests feed it recorded fixtures.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import classify as _classify
from . import doctor, prompts, resume
from .hosts import Host
from .roster import HostRecord, LiveIds, Occupant, Roster, Shape, now_iso
from .slots import SlotStore
from .transport import make_session
from .transport.base import HostSession


@dataclass
class SessionFacts:
    name: str
    status: dict | None = None            # herdr status server --json
    snapshot: dict | None = None          # api snapshot → result.snapshot
    process_info: dict[str, dict] = field(default_factory=dict)  # pane_id → process_info
    excerpts: dict[str, str] = field(default_factory=dict)       # pane_id → text
    error: str | None = None


@dataclass
class HostFacts:
    host: str
    reachable: bool = True
    herdr_path: str | None = None
    pacman_owned: bool = False
    cockpit: bool = False
    sessions: list[SessionFacts] = field(default_factory=list)
    error: str | None = None


# -- live gather (transport-backed, read-only) ----------------------------

def gather(hs: HostSession, *, excerpts: bool = False, excerpt_lines: int = 40) -> SessionFacts:
    st = hs.herdr("status", "server", "--json")
    if not st.ok:
        return SessionFacts(hs.session, error=st.stderr.strip() or "status server failed")
    status = st.json()
    if not status.get("running"):
        return SessionFacts(hs.session, status=status)
    snap = hs.herdr("api", "snapshot")
    if not snap.ok:
        return SessionFacts(hs.session, status=status, error="api snapshot failed")
    snapshot = snap.json().get("snapshot", {})
    facts = SessionFacts(hs.session, status=status, snapshot=snapshot)
    for pane in snapshot.get("panes", []):
        pid = pane["pane_id"]
        pi = hs.herdr("pane", "process-info", "--pane", pid)   # explicit id, never --current
        if pi.ok:
            facts.process_info[pid] = pi.json().get("process_info", {})
        if excerpts:
            rd = hs.herdr("pane", "read", pid, "--source", "recent", "--lines", str(excerpt_lines), "--format", "text")
            if rd.ok:
                facts.excerpts[pid] = rd.stdout
    return facts


def gather_host(host: Host, *, excerpts: bool = False) -> HostFacts:
    hf = HostFacts(host=host.name, cockpit=host.cockpit)
    for name in host.sessions:
        hs = make_session(host, name)
        try:
            hf.sessions.append(gather(hs, excerpts=excerpts))
        except Exception as exc:  # transport errors mark the host, never abort the roll
            hf.reachable = False
            hf.error = str(exc)
            break
    return hf


# -- pure build ---------------------------------------------------------

def _dedupe_workspace_labels(workspaces: list[dict]) -> dict[str, str]:
    """workspace_id → unique label. Two workspaces named ``Work`` in one
    session become ``Work`` and ``Work#5`` (suffix = workspace number)."""
    seen: dict[str, int] = {}
    out: dict[str, str] = {}
    for ws in sorted(workspaces, key=lambda w: w.get("number", 0)):
        label = ws.get("label") or f"ws{ws.get('number')}"
        seen[label] = seen.get(label, 0) + 1
        out[ws["workspace_id"]] = label if seen[label] == 1 else f"{label}#{ws.get('number')}"
    return out


def _pane_order(snapshot: dict) -> dict[str, list[str]]:
    """tab_id → pane ids in layout order (falls back to snapshot order)."""
    order: dict[str, list[str]] = {}
    for lay in snapshot.get("layouts", []):
        order[lay["tab_id"]] = [p["pane_id"] for p in lay.get("panes", [])]
    for pane in snapshot.get("panes", []):
        order.setdefault(pane["tab_id"], [])
        if pane["pane_id"] not in order[pane["tab_id"]]:
            order[pane["tab_id"]].append(pane["pane_id"])
    return order


def build_roster(fleet: str, facts: list[HostFacts], *, slots: SlotStore, allowlist: _classify.Allowlist | None = None,
                 pins: prompts.Pins | None = None, templates: dict[str, str] | None = None,
                 cockpit: dict | None = None, reason: str = "manual", now: str | None = None,
                 keep_excerpts: bool = True) -> Roster:
    now = now or now_iso()
    roster = Roster(fleet=fleet, taken_at=now, reason=reason, cockpit=cockpit or {})
    for hf in facts:
        status0 = next((s.status for s in hf.sessions if s.status), None) or {}
        flavor = doctor.flavor_from(hf.herdr_path, pacman_owned=hf.pacman_owned)
        roster.hosts.append(HostRecord(
            host=hf.host, reachable=hf.reachable, cockpit=hf.cockpit, error=hf.error,
            herdr={"version": status0.get("version"), "protocol": status0.get("protocol"),
                   "flavor": flavor, "socket": status0.get("socket"),
                   "live_handoff": bool((status0.get("capabilities") or {}).get("live_handoff"))},
        ))
        for sf in hf.sessions:
            if not sf.snapshot:
                continue
            snap = sf.snapshot
            ws_labels = _dedupe_workspace_labels(snap.get("workspaces", []))
            tabs = {t["tab_id"]: t for t in snap.get("tabs", [])}
            order = _pane_order(snap)
            rects = {p["pane_id"]: p.get("rect") for lay in snap.get("layouts", []) for p in lay.get("panes", [])}
            shape = Shape(host=hf.host, session=sf.name)
            ws_shapes: dict[str, dict] = {}
            for ws in sorted(snap.get("workspaces", []), key=lambda w: w.get("number", 0)):
                ws_shapes[ws["workspace_id"]] = {
                    "label": ws_labels[ws["workspace_id"]], "number": ws.get("number"),
                    "workspace_id": ws["workspace_id"], "cwd": None, "tabs": [],
                }
            tab_shapes: dict[str, dict] = {}
            for lay in snap.get("layouts", []):
                t = tabs.get(lay["tab_id"], {})
                ts = {"label": str(t.get("label") or t.get("number") or "1"), "number": t.get("number"),
                      "tab_id": lay["tab_id"], "panes": [], "splits": lay.get("splits", []),
                      "zoomed": lay.get("zoomed", False)}
                tab_shapes[lay["tab_id"]] = ts
                ws_shapes[lay["workspace_id"]]["tabs"].append(ts)
            seen_labels: dict[tuple[str, str], int] = {}
            for pane in snap.get("panes", []):
                pid = pane["pane_id"]
                wsid, tid = pane["workspace_id"], pane["tab_id"]
                ws_label = ws_labels.get(wsid, wsid)
                tab = tabs.get(tid, {})
                tab_label = str(tab.get("label") or tab.get("number") or "1")
                idx = order.get(tid, [pid]).index(pid) + 1
                pane_label = pane.get("name") or pane.get("agent_name") or f"p{idx}"
                n = seen_labels[(tid, pane_label)] = seen_labels.get((tid, pane_label), 0) + 1
                if n > 1:   # two agents both named "claude" in one tab must not share a slot
                    pane_label = f"{pane_label}#{idx}"
                human_id = f"{hf.host}/{sf.name}/{ws_label}/{tab_label}/{pane_label}"
                cls = _classify.classify(pane, sf.process_info.get(pid))
                sess = pane.get("agent_session") if cls.role == "agent" else None
                sess_val = sess.get("value") if sess else None
                slot_id = slots.assign(human_id=human_id, host=hf.host, agent_session=sess_val, kind=cls.kind, now=now)
                cwd = pane.get("cwd") or ""
                fg_cwd = pane.get("foreground_cwd") or None
                title = pane.get("terminal_title_stripped") or None
                tasking = title if cls.role == "agent" else (cls.cmdline or None)
                excerpt = sf.excerpts.get(pid) if keep_excerpts else None
                rp = prompts.resolve(human_id=human_id, slot_id=slot_id, role=cls.role, kind=cls.kind,
                                     cwd=fg_cwd or cwd, tasking=tasking, pins=pins, templates=templates,
                                     excerpt=excerpt)
                occ = Occupant(
                    slot_id=slot_id, human_id=human_id, host=hf.host, session=sf.name,
                    workspace_label=ws_label, tab_label=tab_label, pane_label=pane_label,
                    role=cls.role, kind=cls.kind, agent_status=pane.get("agent_status"),
                    argv=list(cls.argv), cmdline=cls.cmdline, cwd=cwd, foreground_cwd=fg_cwd,
                    agent_session=sess, resume_argv=resume.resume_argv(cls.kind, sess_val),
                    resume_prompt=rp, allow_relaunch=_classify.allow_relaunch(cls.role, cls.cmdline, allowlist),
                    live_ids=LiveIds(workspace_id=wsid, tab_id=tid, pane_id=pid, terminal_id=pane.get("terminal_id")),
                    tasking=tasking, excerpt=excerpt, taken_at=now,
                )
                roster.occupants.append(occ)
                if wsid in ws_shapes and ws_shapes[wsid]["cwd"] is None:
                    ws_shapes[wsid]["cwd"] = occ.effective_cwd
                if tid in tab_shapes:
                    tab_shapes[tid]["panes"].append({"pane_label": pane_label, "slot_id": slot_id, "pane_id": pid,
                                                     "cwd": occ.effective_cwd, "rect": rects.get(pid)})
            shape.workspaces = list(ws_shapes.values())
            roster.shapes.append(shape)
    return roster


def strip_excerpts(roster: Roster) -> Roster:
    """Excerpts are cockpit-local unless ``snap.excerpt_sync``."""
    for o in roster.occupants:
        o.excerpt = None
    return roster
