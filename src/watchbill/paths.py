"""XDG paths. Every on-disk location Watchbill uses is defined here.

Config:  ~/.config/watchbill/{hosts.toml,allowlist.txt,pins.toml,config.toml,prompts/<role>.txt}
Data:    ~/.local/share/watchbill/rosters/<fleet>/<stamp>-<reason>.json, current.json (symlink)
         ~/.local/share/watchbill/slots.json
State:   ~/.local/state/watchbill/journal.jsonl
"""
from __future__ import annotations

import os
from pathlib import Path


def _xdg(var: str, default: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / default)


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / "watchbill"


def data_dir() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share") / "watchbill"


def state_dir() -> Path:
    return _xdg("XDG_STATE_HOME", ".local/state") / "watchbill"


def hosts_file() -> Path:
    return config_dir() / "hosts.toml"


def allowlist_file() -> Path:
    return config_dir() / "allowlist.txt"


def pins_file() -> Path:
    return config_dir() / "pins.toml"


def config_file() -> Path:
    return config_dir() / "config.toml"


def prompt_file(role: str) -> Path:
    return config_dir() / "prompts" / f"{role}.txt"


def rosters_dir(fleet: str) -> Path:
    return data_dir() / "rosters" / fleet


def current_roster(fleet: str) -> Path:
    return rosters_dir(fleet) / "current.json"


def slots_file() -> Path:
    return data_dir() / "slots.json"


def journal_file() -> Path:
    return state_dir() / "journal.jsonl"
