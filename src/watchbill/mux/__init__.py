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
    try:
        return _REGISTRY[name](**kw)
    except KeyError:
        raise ValueError(f"unknown mux {name!r}; known: {', '.join(MUXES)}") from None


CAPS: dict[str, Capabilities] = {n: cls().caps for n, cls in _REGISTRY.items()}

__all__ = ["MUXES", "CAPS", "Capabilities", "MuxBackend", "MuxPane", "MuxSnapshot", "MuxTab", "MuxWorkspace",
           "Status", "get", "HerdrBackend", "TmuxBackend", "CmuxBackend"]
