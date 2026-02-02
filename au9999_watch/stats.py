from __future__ import annotations

from collections import deque


class RollingWindow:
    def __init__(self, maxlen: int):
        if maxlen <= 0:
            raise ValueError("maxlen must be > 0")
        self._dq: deque[float] = deque(maxlen=maxlen)

    def push(self, x: float) -> None:
        self._dq.append(float(x))

    def values(self) -> list[float]:
        return list(self._dq)

    def median(self) -> float | None:
        xs = sorted(self._dq)
        if not xs:
            return None
        n = len(xs)
        mid = n // 2
        if n % 2 == 1:
            return xs[mid]
        return 0.5 * (xs[mid - 1] + xs[mid])

    def mad(self) -> float | None:
        """Median absolute deviation (robust scale)."""
        m = self.median()
        if m is None:
            return None
        devs = sorted(abs(x - m) for x in self._dq)
        if not devs:
            return None
        n = len(devs)
        mid = n // 2
        if n % 2 == 1:
            return devs[mid]
        return 0.5 * (devs[mid - 1] + devs[mid])

