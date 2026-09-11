"""Mux backends: which multiplexer owns the PTYs on a host.

``get(name)`` → backend instance. ``CAPS`` → capability matrix for docs and
tests. Backends are pure argv builders + parsers; see base.py.
"""
from __future__ import annotations

from .base import MUXES, Capabilities, MuxBackend, MuxPane, MuxSnapshot, MuxTab, MuxWorkspace, Status
from .cmux import CmuxBackend
from .herdr import HerdrBackend
from .tmux import TmuxBackend

_REGISTRY = {"herdr": HerdrBackend, "tmux": TmuxBackend, "cmux": CmuxBackend}


def get(name: str, **kw) -> MuxBackend:
    """Backend by name. Unknown ``mux_options`` keys are ignored rather than
    raising: hosts.toml is hand-edited and a stray option must not break a
    roll (HerdrBackend takes none at all)."""
    import inspect
    try:
        cls = _REGISTRY[name]
    except KeyError:
        raise ValueError(f"unknown mux {name!r}; known: {', '.join(MUXES)}") from None
    accepted = set(inspect.signature(cls.__init__).parameters) - {"self"}
    return cls(**{k: v for k, v in kw.items() if k in accepted})


CAPS: dict[str, Capabilities] = {n: cls().caps for n, cls in _REGISTRY.items()}

__all__ = ["MUXES", "CAPS", "Capabilities", "MuxBackend", "MuxPane", "MuxSnapshot", "MuxTab", "MuxWorkspace",
           "Status", "get", "HerdrBackend", "TmuxBackend", "CmuxBackend"]
