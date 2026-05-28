# =============================================================================
# File: collector.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/_observability/collector.py
# Description: Subscribes to live Docker container log streams and pushes
#              every line into the LogRingBuffer. One daemon thread per
#              monitored container; the Docker socket is connected ONLY
#              at `start()` (never at construction) so module import has
#              no side effect.
#
#              v2: subscribes to the Docker `events` stream and attaches
#              to NEW `ay-*` containers as they start. Without this, the
#              collector saw only containers up at `start()` time —
#              which in practice misses every Python service that boots
#              after `_obs` itself in the compose stack. The attach
#              path is idempotent (`_monitored` set guarded by a lock)
#              so simultaneous initial-scan + events arrivals do not
#              spawn duplicate streams.
#
# @relation implements:R-100-120
# =============================================================================

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ay_platform_core._observability.buffer import LogEntry, LogRingBuffer
from ay_platform_core._observability.parser import parse_severity

if TYPE_CHECKING:
    from docker.models.containers import Container as DockerContainer

_log = logging.getLogger("c_obs.collector")


class LogCollector:
    """Stream Docker container logs into a LogRingBuffer.

    The Docker client is created lazily in `start()`; the constructor is
    side-effect free so importing this module never touches the host
    daemon. One daemon thread per container handles its own stream; a
    separate daemon thread listens to Docker `events` and dynamically
    attaches new `ay-*` containers as they start. `stop()` signals all
    threads to drain and releases the client.
    """

    def __init__(
        self,
        buffer: LogRingBuffer,
        service_filter_prefix: str = "ay-",
        docker_socket_path: str = "/var/run/docker.sock",
    ) -> None:
        self._buffer = buffer
        self._prefix = service_filter_prefix
        self._docker_socket_path = docker_socket_path
        # `Any` because the docker SDK is intentionally not imported at
        # construction (R-100-120 — module SHALL be import-side-effect-
        # free). `Any` lets the typed methods (`containers.list()`,
        # `events()`, `containers.get()`, `close()`) typecheck without
        # importing docker at the module level.
        self._client: Any = None  # lazily initialised in start()
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []
        # Container IDs for which a stream is already running. Guards
        # the initial-scan vs. events-watcher race and the rare case
        # of a duplicate `start` event.
        self._monitored: set[str] = set()
        self._monitored_lock = threading.Lock()

    # ---- public API --------------------------------------------------------

    def start(self) -> None:
        """Connect to the Docker daemon, attach to every running `ay-*`
        container, and spawn the events watcher for late-arrivals."""
        # Local import is intentional: keeps the module side-effect-free
        # at import time. `docker.DockerClient(...)` connects to the
        # daemon — that MUST not happen during pytest collection / lint.
        import docker  # noqa: PLC0415

        self._client = docker.DockerClient(
            base_url=f"unix://{self._docker_socket_path}"
        )

        # Initial scan: anything already up.
        for c in self._client.containers.list():
            if c.name.startswith(self._prefix):
                self._attach_to(c)

        # Events watcher: catch containers that start later.
        events_thread = threading.Thread(
            target=self._watch_events,
            daemon=True,
            name="obs-events",
        )
        events_thread.start()
        self._threads.append(events_thread)
        _log.info(
            "log collector started — %d initial streams, events watcher live",
            len(self._monitored),
        )

    def stop(self) -> None:
        """Signal all stream threads to exit and release the Docker client."""
        self._stop_event.set()
        if self._client is not None:
            try:
                self._client.close()
            except Exception as exc:  # pragma: no cover — best-effort cleanup
                _log.warning("docker client close failed: %s", exc)
            self._client = None

    # ---- internals ---------------------------------------------------------

    def _attach_to(self, container: DockerContainer) -> bool:
        """Spawn a stream thread for `container` if not already monitored.

        Returns ``True`` when a new thread was spawned, ``False`` when
        the container was already being streamed (idempotent re-entry
        from the events watcher seeing a container that the initial
        scan already attached to, or a duplicate `start` event).
        """
        cid = container.id
        with self._monitored_lock:
            if cid in self._monitored:
                return False
            self._monitored.add(cid)
        t = threading.Thread(
            target=self._stream_one,
            args=(container,),
            daemon=True,
            name=f"obs-{container.name}",
        )
        t.start()
        self._threads.append(t)
        _log.info("attached log stream for %s (%s)", container.name, cid[:12])
        return True

    def _watch_events(self) -> None:
        """Listen to Docker's events stream; attach to new `ay-*` containers.

        Filters server-side to `event=start, type=container` so we only
        receive the events we care about. The stream blocks until a new
        event arrives or the connection is dropped; on connection drop
        the thread exits — `stop()` triggers the same path by closing
        the client.
        """
        if self._client is None:  # pragma: no cover — defensive
            return
        try:
            event_stream = self._client.events(
                decode=True,
                filters={"type": "container", "event": "start"},
            )
        except Exception as exc:
            _log.exception("failed to subscribe to docker events: %s", exc)
            return

        for event in event_stream:
            if self._stop_event.is_set():
                break
            try:
                self._handle_event(event)
            except Exception as exc:  # pragma: no cover — never let one bad event kill the watcher
                _log.warning("event handler raised: %s; event=%r", exc, event)

    def _handle_event(self, event: dict[str, Any]) -> None:
        """Process one Docker event; attach the container if it matches the prefix."""
        if event.get("Type") != "container" or event.get("Action") != "start":
            return
        actor = event.get("Actor") or {}
        attrs = actor.get("Attributes") or {}
        name = str(attrs.get("name", ""))
        if not name.startswith(self._prefix):
            return
        cid = str(actor.get("ID", ""))
        if not cid:
            return
        if self._client is None:  # pragma: no cover — race with stop()
            return
        try:
            container = self._client.containers.get(cid)
        except Exception as exc:
            _log.warning("could not fetch container %s after start event: %s", cid[:12], exc)
            return
        self._attach_to(container)

    def _stream_one(self, container: DockerContainer) -> None:
        """Read every line of a container's log stream, parse, push to buffer."""
        service = container.name.removeprefix(self._prefix) or container.name
        try:
            stream = container.logs(
                stream=True,
                follow=True,
                timestamps=True,
                # Re-attach without replay: the buffer is for live tailing.
                since=int(datetime.now(UTC).timestamp()),
            )
        except Exception as exc:
            _log.exception("failed to attach to %s: %s", container.name, exc)
            return

        for raw in stream:
            if self._stop_event.is_set():
                break
            try:
                line = raw.decode("utf-8", errors="replace").rstrip()
            except Exception:  # pragma: no cover — defensive
                continue

            ts, payload = _split_docker_timestamp(line)
            severity = parse_severity(payload)
            self._buffer.append(
                LogEntry(
                    service=service,
                    timestamp=ts,
                    line=payload,
                    severity=severity,
                )
            )


def _split_docker_timestamp(line: str) -> tuple[datetime, str]:
    """Split the leading RFC3339 timestamp Docker prepends when timestamps=True.

    Falls back to ``datetime.now(UTC)`` and the full line when the timestamp
    is missing or malformed — defensive against future format changes.
    """
    head, sep, rest = line.partition(" ")
    if not sep:
        return datetime.now(UTC), line
    try:
        ts = datetime.fromisoformat(head.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(UTC), line
    return ts, rest
