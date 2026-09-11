"""Process exit codes. Shared by ``cli`` and ``exec``; nothing else imports them.

0 ok · 1 partial (some hosts down) · 2 usage · 3 refused unsafe plan ·
4 transport/action error
"""

OK = 0
PARTIAL = 1
USAGE = 2
REFUSED = 3
TRANSPORT = 4


class WatchbillError(Exception):
    """Base error. ``code`` is the process exit code the CLI maps it to."""

    code = TRANSPORT


class RefusedPlan(WatchbillError):
    """The planner refused to produce a plan it considers unsafe (exit 3).

    Raised for: working agents without ``--force``, label collisions without
    ``--host``, live mode without handoff support, occupant-count guard,
    cockpit host without ``--include-local``.
    """

    code = REFUSED


class UsageError(WatchbillError):
    code = USAGE


class TransportError(WatchbillError):
    code = TRANSPORT
