import tomllib
from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_skill_forbids_overrides_unless_spoken():
    text = (ROOT / "skills/watchbill/SKILL.md").read_text()
    for flag in ("--force", "--include-local", "--allow-reboot", "--force-server-stop", "--mode live"):
        assert flag in text
    never = text.split("## Never")[1].split("## Verbs")[0]
    assert all(f in never for f in ("--force", "--include-local", "--allow-reboot"))
    assert "dry-run" in text and "herdr machine" in text


def test_plugin_manifest_is_a_thin_wrapper():
    m = tomllib.loads((ROOT / "plugin/herdr-plugin.toml").read_text())
    assert m["id"] == "sfl.watchbill" and m["min_herdr_version"] == "0.8.2"
    assert m["actions"]
    for a in m["actions"]:
        assert a["command"][0] == "watchbill"
        assert "--yes" not in a["command"]


def test_docs_exist_with_sequences():
    arch = (ROOT / "docs/architecture.md").read_text()
    for name in ("roll", "secure park", "set", "relieve cold", "relieve live-rejected", "upgrade-agents"):
        assert name in arch
    assert "UNVERIFIED-0.8.2" in arch
    assert "--remote" in (ROOT / "README.md").read_text()


def test_no_forbidden_dependencies_or_calls():
    src = "\n".join(p.read_text() for p in (ROOT / "src").rglob("*.py"))
    for bad in ("herdr-resurrect", "herdr-hub", "herdr-suspend-workspace", "herdr-muster", "kichel.muster"):
        assert bad not in src
    assert '"machine"' not in src.replace('v[0] == "machine"', "")   # only the guard mentions it
