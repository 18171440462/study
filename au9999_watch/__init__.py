"""AU9999 watch: simple polling + heuristic flow signals.

This package intentionally uses only Python stdlib so it can run in minimal
environments.
"""

from .types import Quote, SignalSnapshot

__all__ = ["Quote", "SignalSnapshot"]

