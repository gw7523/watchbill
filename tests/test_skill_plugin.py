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
    from watchbill import PLUGIN_ID
    assert m["id"] == PLUGIN_ID == "agents.watchbill" and m["min_herdr_version"] == "0.8.2"
    # nothing in this tool is SFL-specific; the namespace must not drift back
    assert not m["id"].startswith("sfl."), "watchbill is a personal, Apache-2.0 tool"
    assert m["actions"]
    for a in m["actions"]:
        assert a["command"][0] == "watchbill"
        assert "--yes" not in a["command"]


def test_old_sfl_plugin_id_appears_nowhere():
    old = "sfl" + ".watchbill"          # spelled apart so this file does not match itself
    hits = [str(p.relative_to(ROOT)) for p in ROOT.rglob("*")
            if p.is_file() and ".git" not in p.parts and ".venv" not in p.parts
            and p.suffix in (".py", ".md", ".toml", ".json", ".txt") and old in p.read_text(errors="ignore")]
    assert hits == [], f"old plugin id still present in: {hits}"


def test_docs_exist_with_sequences():
    arch = (ROOT / "docs/architecture.md").read_text()
    for name in ("roll", "secure park", "set", "relieve cold", "relieve live-rejected", "upgrade-agents"):
        assert name in arch
    assert "UNVERIFIED-0.8.2" in arch
    assert "--remote" in (ROOT / "README.md").read_text()


def test_agent_bootstrap_files():
    agents = (ROOT / "AGENTS.md").read_text()
    for must in ("docs/kickoff-prompt.md", "docs/architecture.md", "docs/herdr-0.8.2-facts.md", "implement MVP next",
                 "uv run pytest", "--force", "herdr update", "omarchy-update"):
        assert must in agents
    assert "AGENTS.md" in (ROOT / "CLAUDE.md").read_text()


def test_no_forbidden_dependencies_or_calls():
    src = "\n".join(p.read_text() for p in (ROOT / "src").rglob("*.py"))
    for bad in ("herdr-resurrect", "herdr-hub", "herdr-suspend-workspace", "herdr-muster", "kichel.muster"):
        assert bad not in src
    assert '"machine"' not in src.replace('v[0] == "machine"', "")   # only the guard mentions it
