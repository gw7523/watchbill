"""``HostSession`` — the client protocol every transport implements.

One ``HostSession`` is one (host, herdr session) pair. It exposes two
primitives and two argv builders:

* ``herdr(*args)``   run ``herdr --session <S> <args>`` on the host
* ``shell(argv)``    run an arbitrary argv on the host (action commands)
* ``herdr_argv``/``shell_argv``  the exact argv the primitive *would* run —
  planners embed these in ``Step.argv`` so dry-run shows the literal command.

Transports do not police mutation; ``plan.is_mutating`` and ``exec`` do.
Transports never read OAuth or credential files and never rewrite base URLs.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, Sequence

if TYPE_CHECKING:
    from ..mux.base import MuxBackend

from ..exitcodes import TransportError  # noqa: F401  (re-exported for transports)
from ..hosts import Host


class NotImplementedInThisPass(TransportError):
    """Raised by live primitives in the architecture/skeleton pass."""

    def __init__(self, what: str):
        super().__init__(f"{what}: not implemented in this pass (architecture + skeleton). "
                         "Say 'implement MVP next' to add local + ssh_cli transports.")


@dataclass(frozen=True)
class CmdResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def json(self) -> dict:
        """Parse Herdr CLI JSON (``{"id": ..., "result": {...}}``) → result."""
        data = json.loads(self.stdout)
        if isinstance(data, dict) and "result" in data:
            return data["result"]
        return data


class HostSession(Protocol):
    host: Host
    session: str
    backend: "MuxBackend"

    def mux_argv(self, *args: str) -> list[str]: ...
    def herdr_argv(self, *args: str) -> list[str]: ...        # alias of mux_argv (herdr mux)
    def shell_argv(self, argv: Sequence[str]) -> list[str]: ...
    def mux(self, *args: str, timeout: float = 30.0) -> CmdResult: ...
    def herdr(self, *args: str, timeout: float = 30.0) -> CmdResult: ...
    def shell(self, argv: Sequence[str], timeout: float = 60.0) -> CmdResult: ...
    def reachable(self) -> bool: ...


TIMEOUT_RC = 124      # conventional "timed out"
NOTFOUND_RC = 127     # conventional "command not found"


def run_argv(argv: Sequence[str], *, timeout: float, scrub: Sequence[str] = (), cwd: str | None = None) -> CmdResult:
    """Spawn one process and never raise. The only ``subprocess`` call in the
    package outside ``exec.local_runner``.

    * never ``shell=True`` — argv is argv
    * ``scrub`` removes env vars that would redirect the mux (``TMUX`` makes
      tmux think it is nested; ``HERDR_SOCKET_PATH`` outranks ``HERDR_SESSION``)
    * a timeout or a missing binary comes back as a failed :class:`CmdResult`,
      so ``exec`` journals it as a step failure instead of unwinding the run
    """
    argv = [str(a) for a in argv]
    env = dict(os.environ)
    for k in scrub:
        env.pop(k, None)
    try:
        cp = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env, cwd=cwd, check=False)
    except FileNotFoundError:
        return CmdResult(tuple(argv), NOTFOUND_RC, "", f"command not found: {argv[0]}")
    except PermissionError as exc:
        return CmdResult(tuple(argv), NOTFOUND_RC, "", str(exc))
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or ""
        err = exc.stderr or ""
        if isinstance(out, bytes):
            out = out.decode(errors="replace")
        if isinstance(err, bytes):
            err = err.decode(errors="replace")
        return CmdResult(tuple(argv), TIMEOUT_RC, out, (err + f"\ntimed out after {timeout}s").strip())
    return CmdResult(tuple(argv), cp.returncode, cp.stdout or "", cp.stderr or "")


def scrub_for(host: Host) -> tuple[str, ...]:
    """Env vars to drop before running this host's mux CLI locally."""
    if host.mux == "tmux":
        return ("TMUX", "TMUX_PANE")          # otherwise tmux refuses: "sessions should be nested with care"
    if host.mux == "herdr":
        return ("HERDR_SOCKET_PATH",)         # outranks --session's sibling env; we always pass --session
    if host.mux == "cmux":
        return ("CMUX_SOCKET_PATH",) if host.mux_options.get("socket") else ()
    return ()


def backend_for(host: Host) -> "MuxBackend":
    from .. import mux as _mux
    return _mux.get(host.mux, **host.mux_options)


def mux_prefix(host: Host, session: str) -> list[str]:
    """CLI prefix of the host's mux: ``herdr --session S`` (named explicitly
    even for default, so a remote HERDR_SESSION cannot redirect us),
    ``tmux -L S``, or ``cmux [--socket P]``."""
    prefix = backend_for(host).cli_prefix(session)
    if host.mux == "herdr":
        prefix[0] = host.herdr_bin
    return prefix


def herdr_session_args(host: Host, session: str) -> list[str]:
    return mux_prefix(host, session)
