import os

from watchbill import paths


def test_watchbill_home_isolates_without_touching_xdg(monkeypatch, tmp_path):
    """Isolation must not redirect the mux: herdr keeps its sockets under
    $XDG_CONFIG_HOME/herdr, so WATCHBILL_HOME is the only safe override."""
    monkeypatch.setenv("WATCHBILL_HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert paths.hosts_file() == tmp_path / "config" / "hosts.toml"
    assert paths.journal_file() == tmp_path / "state" / "journal.jsonl"
    assert paths.slots_file() == tmp_path / "data" / "slots.json"
    assert "XDG_CONFIG_HOME" not in os.environ
