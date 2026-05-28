# =============================================================================
# File: test_buffer.py
# Version: 1
# Path: ay_platform_core/tests/unit/_observability/test_buffer.py
# Description: Behaviour of LogRingBuffer: append, eviction at capacity,
#              filtering on tail (service / since / min_severity / limit),
#              digest counts, clear, services listing.
# =============================================================================

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ay_platform_core._observability.buffer import LogEntry, LogRingBuffer

pytestmark = pytest.mark.unit


def _entry(
    service: str = "c2",
    seconds_ago: int = 0,
    severity: str = "INFO",
    line: str = "msg",
) -> LogEntry:
    return LogEntry(
        service=service,
        timestamp=datetime.now(UTC) - timedelta(seconds=seconds_ago),
        line=line,
        severity=severity,
    )


class TestRingBuffer:
    def test_init_validates_capacity(self) -> None:
        with pytest.raises(ValueError):
            LogRingBuffer(max_per_service=0)

    def test_append_and_tail_default(self) -> None:
        buf = LogRingBuffer(max_per_service=10)
        for i in range(3):
            buf.append(_entry(line=f"line-{i}"))
        result = buf.tail(limit=10)
        assert [e.line for e in result] == ["line-2", "line-1", "line-0"][::-1]

    def test_eviction_at_capacity(self) -> None:
        buf = LogRingBuffer(max_per_service=2)
        for i in range(5):
            buf.append(_entry(line=f"l{i}", seconds_ago=5 - i))
        # Only the two most recent SHALL remain (l3 is older than l4 here
        # because seconds_ago decreases — l4 has seconds_ago=1, l3=2).
        lines = sorted(e.line for e in buf.tail(limit=10))
        assert lines == ["l3", "l4"]

    def test_isolation_between_services(self) -> None:
        buf = LogRingBuffer(max_per_service=5)
        buf.append(_entry(service="c2", line="a"))
        buf.append(_entry(service="c5", line="b"))
        c2_only = buf.tail(service="c2", limit=10)
        c5_only = buf.tail(service="c5", limit=10)
        assert [e.line for e in c2_only] == ["a"]
        assert [e.line for e in c5_only] == ["b"]

    def test_tail_unknown_service_returns_empty(self) -> None:
        buf = LogRingBuffer(max_per_service=5)
        buf.append(_entry(service="c2", line="x"))
        assert buf.tail(service="cZ", limit=10) == []

    def test_filter_since(self) -> None:
        buf = LogRingBuffer(max_per_service=10)
        buf.append(_entry(line="old", seconds_ago=120))
        buf.append(_entry(line="new", seconds_ago=10))
        cutoff = datetime.now(UTC) - timedelta(seconds=60)
        result = buf.tail(since=cutoff, limit=10)
        assert [e.line for e in result] == ["new"]

    def test_filter_min_severity(self) -> None:
        buf = LogRingBuffer(max_per_service=10)
        buf.append(_entry(line="info", severity="INFO"))
        buf.append(_entry(line="warn", severity="WARNING"))
        buf.append(_entry(line="error", severity="ERROR"))
        buf.append(_entry(line="crit", severity="CRITICAL"))
        result = buf.tail(min_severity="ERROR", limit=10)
        assert sorted(e.line for e in result) == ["crit", "error"]

    def test_limit_keeps_most_recent(self) -> None:
        buf = LogRingBuffer(max_per_service=10)
        for i in range(5):
            buf.append(_entry(line=f"l{i}", seconds_ago=5 - i))
        result = buf.tail(limit=2)
        # Two newest = the ones with smallest seconds_ago = l4 then l3.
        # tail returns newest-LAST so the order is [l3, l4].
        assert [e.line for e in result] == ["l3", "l4"]

    def test_limit_zero_returns_empty(self) -> None:
        buf = LogRingBuffer(max_per_service=10)
        buf.append(_entry(line="x"))
        assert buf.tail(limit=0) == []

    def test_clear_drops_everything(self) -> None:
        buf = LogRingBuffer(max_per_service=10)
        buf.append(_entry(service="c2"))
        buf.append(_entry(service="c5"))
        buf.clear()
        assert buf.services() == []
        assert buf.tail(limit=10) == []

    def test_services_listing(self) -> None:
        buf = LogRingBuffer(max_per_service=5)
        for s in ("c2", "c5", "c2"):
            buf.append(_entry(service=s))
        # Sorted, no duplicates.
        assert buf.services() == ["c2", "c5"]

    def test_digest_counts_per_severity(self) -> None:
        buf = LogRingBuffer(max_per_service=10)
        buf.append(_entry(service="c2", severity="INFO"))
        buf.append(_entry(service="c2", severity="ERROR"))
        buf.append(_entry(service="c5", severity="ERROR"))
        d = buf.digest()
        assert d["c2"]["INFO"] == 1
        assert d["c2"]["ERROR"] == 1
        assert d["c2"]["CRITICAL"] == 0
        assert d["c5"]["ERROR"] == 1

    def test_digest_empty_returns_empty_dict(self) -> None:
        assert LogRingBuffer().digest() == {}
