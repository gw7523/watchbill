import json
from pathlib import Path

import jsonschema

from watchbill.roster import Roster

SCHEMA = json.loads((Path(__file__).parent.parent / "docs" / "roster.schema.json").read_text())


def validator():
    jsonschema.Draft202012Validator.check_schema(SCHEMA)
    return jsonschema.Draft202012Validator(SCHEMA, format_checker=jsonschema.FormatChecker())


def test_sample_roster_validates(roster):
    d = roster.to_dict()
    validator().validate(d)
    assert d["schema"] == 1 and len(d["occupants"]) == 11
    for key in ("slot_id", "human_id", "role", "agent_session", "resume_argv", "resume_prompt", "allow_relaunch", "live_ids"):
        assert key in d["occupants"][0]


def test_roundtrip(roster):
    again = Roster.from_dict(json.loads(json.dumps(roster.to_dict())))
    assert again.to_dict() == roster.to_dict()


def test_schema_rejects_drift(roster):
    v = validator()
    d = roster.to_dict()
    del d["occupants"][0]["slot_id"]
    assert list(v.iter_errors(d))
    d = roster.to_dict()
    d["occupants"][0]["role"] = "daemon"
    assert list(v.iter_errors(d))
    d = roster.to_dict()
    d["occupants"][0]["live_ids"]["pane_index"] = 3   # live_ids is closed: no new hint fields without a schema bump
    assert list(v.iter_errors(d))
    d = roster.to_dict()
    d["schema"] = 2
    assert list(v.iter_errors(d))
