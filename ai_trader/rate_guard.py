"""Rate & Cost Guard — per-provider sliding-window call budget.

Keeps continuous polling (Phase 3) inside each free tier's limits instead of
letting it die at minute 20 (plan §2.2). Enforces calls-per-minute and a minimum
interval between calls. Phase 3 extends rotation on top of this same interface.
"""

from __future__ import annotations

import time
from collections import deque

_WINDOW_SECONDS = 60.0


class RateGuard:
    """Tracks recent calls per provider and answers 'may I call now?'."""

    def __init__(self, calls_per_minute: int = 6, min_interval_seconds: float = 8.0) -> None:
        if calls_per_minute <= 0:
            raise ValueError("calls_per_minute must be > 0")
        self.calls_per_minute = calls_per_minute
        self.min_interval_seconds = min_interval_seconds
        self._calls: dict[str, deque[float]] = {}
        self._last: dict[str, float] = {}

    def _prune(self, name: str, now: float) -> None:
        calls = self._calls.get(name)
        if not calls:
            return
        while calls and now - calls[0] > _WINDOW_SECONDS:
            calls.popleft()

    def should_allow(self, name: str, now: float | None = None) -> bool:
        """Atomically check the budget and, if allowed, record the call.

        Returns True and consumes one call-slot, or False (no record).
        """
        now = now if now is not None else time.monotonic()
        self._prune(name, now)
        calls = self._calls.setdefault(name, deque())
        if len(calls) >= self.calls_per_minute:
            return False
        if now - self._last.get(name, -self.min_interval_seconds) < self.min_interval_seconds:
            return False
        calls.append(now)
        self._last[name] = now
        return True

    def remaining(self, name: str, now: float | None = None) -> int:
        """Number of call-slots left in the current window for ``name``."""
        now = now if now is not None else time.monotonic()
        self._prune(name, now)
        return max(0, self.calls_per_minute - len(self._calls.get(name, ())))

    def reset(self, name: str | None = None) -> None:
        """Clear recorded calls for one provider (or all when ``name`` is None)."""
        if name is None:
            self._calls.clear()
            self._last.clear()
        else:
            self._calls.pop(name, None)
            self._last.pop(name, None)
