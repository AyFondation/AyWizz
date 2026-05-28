# =============================================================================
# File: buffer.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/_observability/buffer.py
# Description: Thread-safe per-service ring buffer for log entries. Bounded
#              by `max_per_service` lines per service; oldest lines are
#              dropped. Reads are filterable by service name, lower-bound
#              timestamp, and minimum severity.
#
# @relation implements:R-100-120
# =============================================================================

from __future__ import annotations

import threading
from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from ay_platform_core._observability.parser import SEVERITY_RANK, is_at_least


@dataclass(frozen=True)
class LogEntry:
    """One log line captured from a container's stdout/stderr stream."""

    service: str
    timestamp: datetime
    line: str
    severity: str  # one of SEVERITY_RANK


class LogRingBuffer:
    """Per-service bounded buffer.

    Each service gets its own deque of ``max_per_service`` entries. Older
    entries are evicted on overflow. All public methods are thread-safe.
    """

    def __init__(self, max_per_service: int = 5000) -> None:
        if max_per_service < 1:
            raise ValueError("max_per_service must be >= 1")
        self._max = max_per_service
        self._buffers: dict[str, deque[LogEntry]] = defaultdict(
            lambda: deque(maxlen=self._max)
        )
        self._lock = threading.Lock()

    def append(self, entry: LogEntry) -> None:
        """Append a single log entry. O(1)."""
        with self._lock:
            self._buffers[entry.service].append(entry)

    def services(self) -> list[str]:
        """Sorted list of service names that have at least one entry."""
        with self._lock:
            return sorted(self._buffers.keys())

    def clear(self) -> None:
        """Drop every buffered entry. Used between tests."""
        with self._lock:
            self._buffers.clear()

    def tail(
        self,
        *,
        service: str | None = None,
        since: datetime | None = None,
        min_severity: str | None = None,
        limit: int = 1000,
    ) -> list[LogEntry]:
        """Return the most recent matching entries (newest last).

        Filters compose by AND. ``limit`` caps the result size; it is
        applied AFTER filtering so the caller always sees the most
        recent matches even when many older entries are present.
        """
        if limit < 1:
            return []
        with self._lock:
            sources: Iterable[Iterable[LogEntry]]
            if service is None:
                sources = list(self._buffers.values())
            else:
                sources = [list(self._buffers.get(service, ()))]
            # Snapshot so we can release the lock before any sort work.
            snapshot: list[LogEntry] = [e for src in sources for e in src]

        if since is not None:
            snapshot = [e for e in snapshot if e.timestamp >= since]
        if min_severity is not None:
            snapshot = [e for e in snapshot if is_at_least(e.severity, min_severity)]

        # Newest-last ordering across services.
        snapshot.sort(key=lambda e: e.timestamp)
        if len(snapshot) > limit:
            snapshot = snapshot[-limit:]
        return snapshot

    def digest(self) -> dict[str, dict[str, int]]:
        """Return a count of buffered entries per service per severity."""
        with self._lock:
            snapshot: dict[str, list[LogEntry]] = {
                name: list(buf) for name, buf in self._buffers.items()
            }
        out: dict[str, dict[str, int]] = {}
        for service, entries in snapshot.items():
            counts: dict[str, int] = {sev: 0 for sev in SEVERITY_RANK}
            for entry in entries:
                if entry.severity in counts:
                    counts[entry.severity] += 1
            out[service] = counts
        return out
