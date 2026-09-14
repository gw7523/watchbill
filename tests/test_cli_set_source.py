"""`set` after `secure park`: the post-secure snap retargets current.json to a
roster in which the parked agents are shells, so a plain `set` restored
nothing (Mac tmux, 2026-09-14). The source is the newest pre-secure roster."""
import json
from pathlib import Path

from watchbill.cli import set_source


def _w(p: Path, reason: str):
    p.write_text(json.dumps({"reason": reason, "occupants": []}))


def test_set_falls_in_from_the_pre_secure_roster(tmp_path):
    cur = tmp_path / "current.json"
    _w(tmp_path / "20260914T141932Z-pre-secure.json", "pre-secure")
    _w(tmp_path / "20260914T141935Z-post-secure.json", "post-secure")
    _w(cur, "post-secure")
    src, why = set_source(cur)
    assert src.name == "20260914T141932Z-pre-secure.json" and "falling in from" in why
    _w(cur, "post-set")
    assert set_source(cur) == (cur, None)
    assert set_source(cur, tmp_path / "x.json") == (tmp_path / "x.json", None)
    _w(cur, "post-secure")
    for f in tmp_path.glob("*-pre-secure.json"):
        f.unlink()
    src, why = set_source(cur)
    assert src == cur and "nothing to restore" in why
