# =============================================================================
# File: test_collector.py
# Version: 1
# Path: ay_platform_core/tests/unit/_observability/test_collector.py
# Description: Unit tests for the LogCollector pieces that don't require a
#              live Docker daemon — `_attach_to` idempotency and
#              `_handle_event` dispatch logic. The streaming itself is
#              covered by the live stack smoke (a unit test of the actual
#              Docker SDK call would just mirror its mock).
# =============================================================================

from __future__ import annotations

import threading
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest

from ay_platform_core._observability.buffer import LogRingBuffer
from ay_platform_core._observability.collector import LogCollector

pytestmark = pytest.mark.unit


@dataclass
class _FakeContainer:
    """Minimal stand-in for docker.models.containers.Container."""

    id: str
    name: str

    def logs(self, **_: Any) -> list[bytes]:  # pragma: no cover — never invoked
        return []


@pytest.fixture
def collector_with_recorder(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[LogCollector, list[str]]]:
    """Stub `_stream_one` so `_attach_to` does not actually start a stream;
    instead it records each container it would have attached to."""
    buffer = LogRingBuffer(max_per_service=10)
    collector = LogCollector(buffer=buffer)

    attached: list[str] = []

    def _fake_stream(self: LogCollector, container: _FakeContainer) -> None:
        attached.append(container.id)

    monkeypatch.setattr(LogCollector, "_stream_one", _fake_stream)
    # Replace Thread so attaching is synchronous (avoids waiting for the
    # daemon to schedule).
    real_thread = threading.Thread

    class _SyncThread:
        def __init__(self, target: Any, args: tuple[Any, ...], **_: Any) -> None:
            self._target = target
            self._args = args

        def start(self) -> None:
            self._target(*self._args)

    monkeypatch.setattr(
        "ay_platform_core._observability.collector.threading.Thread",
        _SyncThread,
    )
    yield collector, attached
    monkeypatch.setattr(
        "ay_platform_core._observability.collector.threading.Thread",
        real_thread,
    )


class TestAttachIdempotency:
    def test_first_attach_spawns_stream(
        self, collector_with_recorder: tuple[LogCollector, list[str]]
    ) -> None:
        collector, attached = collector_with_recorder
        c = _FakeContainer(id="abc123", name="ay-c2-auth")
        assert collector._attach_to(c) is True
        assert attached == ["abc123"]
        assert "abc123" in collector._monitored

    def test_second_attach_is_skipped(
        self, collector_with_recorder: tuple[LogCollector, list[str]]
    ) -> None:
        collector, attached = collector_with_recorder
        c = _FakeContainer(id="abc123", name="ay-c2-auth")
        assert collector._attach_to(c) is True
        # Second call with the same container ID returns False, no extra
        # stream spawned.
        assert collector._attach_to(c) is False
        assert attached == ["abc123"]

    def test_distinct_containers_each_spawn(
        self, collector_with_recorder: tuple[LogCollector, list[str]]
    ) -> None:
        collector, attached = collector_with_recorder
        a = _FakeContainer(id="aaa", name="ay-c2-auth")
        b = _FakeContainer(id="bbb", name="ay-c5-requirements")
        collector._attach_to(a)
        collector._attach_to(b)
        assert attached == ["aaa", "bbb"]


class TestEventDispatch:
    """`_handle_event` filters Docker events; only `container/start` events
    whose container name starts with the prefix trigger an attach."""

    def _patch_get(
        self,
        collector: LogCollector,
        monkeypatch: pytest.MonkeyPatch,
        *,
        return_container: _FakeContainer | None = None,
        raises: Exception | None = None,
    ) -> None:
        class _FakeContainersAPI:
            def get(self, cid: str) -> _FakeContainer:
                if raises is not None:
                    raise raises
                if return_container is None:
                    raise AssertionError("test forgot to set return_container")
                return return_container

        class _FakeClient:
            containers = _FakeContainersAPI()

        # Inject the fake client directly. `_client` is `Any` in production
        # so this assignment is type-clean.
        collector._client = _FakeClient()

    def test_start_event_with_matching_prefix_attaches(
        self,
        collector_with_recorder: tuple[LogCollector, list[str]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        collector, attached = collector_with_recorder
        target = _FakeContainer(id="ccc", name="ay-c5-requirements")
        self._patch_get(collector, monkeypatch, return_container=target)
        event = {
            "Type": "container",
            "Action": "start",
            "Actor": {"ID": "ccc", "Attributes": {"name": "ay-c5-requirements"}},
        }
        collector._handle_event(event)
        assert attached == ["ccc"]

    def test_start_event_without_prefix_is_ignored(
        self,
        collector_with_recorder: tuple[LogCollector, list[str]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        collector, attached = collector_with_recorder
        # No `_patch_get` — the dispatch SHOULD short-circuit before
        # touching the client.
        event = {
            "Type": "container",
            "Action": "start",
            "Actor": {"ID": "ddd", "Attributes": {"name": "unrelated-redis"}},
        }
        collector._handle_event(event)
        assert attached == []

    def test_non_start_action_is_ignored(
        self,
        collector_with_recorder: tuple[LogCollector, list[str]],
    ) -> None:
        collector, attached = collector_with_recorder
        event = {
            "Type": "container",
            "Action": "die",
            "Actor": {"ID": "eee", "Attributes": {"name": "ay-c4-orchestrator"}},
        }
        collector._handle_event(event)
        assert attached == []

    def test_non_container_event_is_ignored(
        self,
        collector_with_recorder: tuple[LogCollector, list[str]],
    ) -> None:
        collector, attached = collector_with_recorder
        event = {
            "Type": "image",
            "Action": "pull",
            "Actor": {"ID": "fff", "Attributes": {"name": "ay-c4-orchestrator"}},
        }
        collector._handle_event(event)
        assert attached == []

    def test_get_failure_is_swallowed(
        self,
        collector_with_recorder: tuple[LogCollector, list[str]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If the container disappears between the start event and the
        `get()` call, log a warning and move on — never propagate."""
        collector, attached = collector_with_recorder
        self._patch_get(
            collector, monkeypatch, raises=RuntimeError("gone")
        )
        event = {
            "Type": "container",
            "Action": "start",
            "Actor": {"ID": "ggg", "Attributes": {"name": "ay-c2-auth"}},
        }
        # Must not raise.
        collector._handle_event(event)
        assert attached == []

    def test_event_with_already_monitored_id_is_idempotent(
        self,
        collector_with_recorder: tuple[LogCollector, list[str]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Initial scan + race with events: same container twice is fine."""
        collector, attached = collector_with_recorder
        target = _FakeContainer(id="hhh", name="ay-c4-orchestrator")
        self._patch_get(collector, monkeypatch, return_container=target)
        # Pre-attach via the initial-scan path.
        collector._attach_to(target)
        # Then receive a duplicate start event.
        event = {
            "Type": "container",
            "Action": "start",
            "Actor": {"ID": "hhh", "Attributes": {"name": "ay-c4-orchestrator"}},
        }
        collector._handle_event(event)
        # Only one attach — the second was deduplicated.
        assert attached == ["hhh"]
