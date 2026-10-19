"""Per-parameter time series with as-of lookups.

Out-of-order arrivals are normal in EMR feeds (a lab resulted late, a charted
vital back-timed), so points are kept sorted by observation time and every
lookup is relative to an explicit ``at`` time.
"""

from __future__ import annotations

import bisect
from datetime import datetime, timedelta
from typing import Any, Iterator


class Series:
    __slots__ = ("_times", "_values")

    def __init__(self):
        self._times: list[datetime] = []
        self._values: list[Any] = []

    def add(self, t: datetime, value: Any) -> None:
        i = bisect.bisect_right(self._times, t)
        # A corrected result at the same timestamp replaces the earlier one.
        if i and self._times[i - 1] == t:
            self._values[i - 1] = value
            return
        self._times.insert(i, t)
        self._values.insert(i, value)

    def __len__(self) -> int:
        return len(self._times)

    def __iter__(self) -> Iterator[tuple[datetime, Any]]:
        return iter(zip(self._times, self._values))

    def latest(self, at: datetime, max_age: timedelta | None = None) -> tuple[datetime, Any] | None:
        """Most recent point at or before ``at`` (and no older than ``max_age``)."""
        i = bisect.bisect_right(self._times, at)
        if not i:
            return None
        t = self._times[i - 1]
        if max_age is not None and at - t > max_age:
            return None
        return t, self._values[i - 1]

    def window(self, start: datetime, end: datetime) -> list[tuple[datetime, Any]]:
        """Points with start <= t <= end."""
        lo = bisect.bisect_left(self._times, start)
        hi = bisect.bisect_right(self._times, end)
        return list(zip(self._times[lo:hi], self._values[lo:hi]))

    def previous(self, at: datetime, n: int = 1) -> list[tuple[datetime, Any]]:
        """The ``n`` points before the latest one at ``at`` (oldest first)."""
        i = bisect.bisect_right(self._times, at)
        lo = max(0, i - 1 - n)
        return list(zip(self._times[lo:i - 1], self._values[lo:i - 1]))
