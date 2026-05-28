# =============================================================================
# File: client.py
# Version: 3
# Path: ay_platform_core/src/ay_platform_core/c8_llm/client.py
# Description: Python client for the C8 LLM gateway. All internal components
#              (C3, C4, C6, C7, …) use this class rather than importing
#              LiteLLM directly (R-800-011 policy). Enforces the mandatory
#              agent/session headers (R-800-013) at the call site so no
#              component can accidentally bypass cost attribution.
#
#              v3 (2026-05-20) : per-agent route resolver client-side
#              (R-800-030 v1 note). Loaded from `agent_routes:` in the
#              litellm YAML OR an inline JSON env override ; resolves
#              `agent_name → model_name` before every request when the
#              caller leaves `model` unset. Proxy is off-the-shelf
#              LiteLLM ; Q-800-011 tracks proxy-side admission for v2.
#
#              v2 (2026-05-19): `chat_completion` now retries HTTP 429
#              (provider rate-limit) up to 3 attempts, honouring the
#              `Retry-After` header / OpenRouter `retry_after_seconds`
#              (clamped 20 s). Free hosted tiers throttle intermittently
#              mid tool-loop ; a bounded honoured retry smooths it over
#              instead of failing the whole DocGen turn. Non-429
#              non-200 still raises immediately (unchanged).
#
# @relation implements:R-800-010
# @relation implements:R-800-011
# @relation implements:R-800-013
# @relation implements:R-800-014
# @relation implements:R-800-030
# @relation implements:R-800-073
# =============================================================================

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx

from ay_platform_core.c8_llm.config import ClientSettings
from ay_platform_core.c8_llm.models import (
    BudgetStatus,
    ChatCompletionRequest,
    ChatCompletionResponse,
    CostSummary,
)
from ay_platform_core.observability import make_traced_client


class LLMGatewayError(RuntimeError):
    """Raised on any non-success response from the gateway."""

    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"LLM gateway returned {status_code}: {body}")
        self.status_code = status_code
        self.body = body


# Bounded retry for HTTP 429 (provider rate-limit). Free hosted tiers
# (OpenRouter `:free`, etc.) throttle intermittently and return a
# Retry-After ; honouring it for a couple of attempts smooths over
# the transient throttle instead of failing the whole DocGen turn.
# Capped so a hostile/huge delay can't wedge the request.
_RETRY_429_MAX_ATTEMPTS = 3
_RETRY_429_CAP_SECONDS = 20.0
_RETRY_429_DEFAULT_SECONDS = 5.0


def _routes_from_inline(raw: str) -> dict[str, str] | None:
    """Parse `C8_AGENT_ROUTES_INLINE` JSON. None when malformed/empty."""
    import logging  # noqa: PLC0415 — cold path

    log = logging.getLogger("c8_llm.client")
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        log.warning("C8_AGENT_ROUTES_INLINE not valid JSON, ignoring: %s", exc)
        return {}
    if not isinstance(parsed, dict):
        log.warning(
            "C8_AGENT_ROUTES_INLINE must be a JSON object, got %s",
            type(parsed).__name__,
        )
        return {}
    return {str(k): str(v) for k, v in parsed.items() if isinstance(v, str)}


def _routes_from_yaml(path: str) -> dict[str, str]:
    """Parse `agent_routes:` from the LiteLLM YAML. Always returns a
    dict (empty on any failure ; a WARNING is logged)."""
    import logging  # noqa: PLC0415 — cold path

    log = logging.getLogger("c8_llm.client")
    try:
        import yaml  # noqa: PLC0415 — PyYAML is an optional dependency
    except ImportError:
        log.warning("PyYAML not installed ; C8_AGENT_ROUTES_YAML_PATH ignored")
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except OSError as exc:
        log.warning("agent_routes YAML %s unreadable: %s", path, exc)
        return {}
    except yaml.YAMLError as exc:
        log.warning("agent_routes YAML %s malformed: %s", path, exc)
        return {}
    routes = data.get("agent_routes") if isinstance(data, dict) else None
    if not isinstance(routes, dict):
        return {}
    return {str(k): str(v) for k, v in routes.items() if isinstance(v, str)}


def _load_agent_routes(settings: ClientSettings) -> dict[str, str]:
    """Build the in-memory `agent_name → model_name` table from the
    ClientSettings, evaluating the two sources in priority order :

    1. `C8_AGENT_ROUTES_INLINE` (JSON object) — useful for tests and
       dev overrides without a YAML on disk.
    2. `C8_AGENT_ROUTES_YAML_PATH` — the `agent_routes:` section of
       the same `litellm-config.yaml` LiteLLM consumes (single
       source-of-truth shape per R-800-024).

    Failure modes (missing file, malformed JSON, malformed YAML) log
    a WARNING and yield an empty dict — the client then falls back to
    `default_model` for every agent, which is the safe behaviour.
    """
    inline = _routes_from_inline((settings.agent_routes_inline or "").strip())
    if inline is not None:
        return inline
    yaml_path = (settings.agent_routes_yaml_path or "").strip()
    if not yaml_path:
        return {}
    return _routes_from_yaml(yaml_path)


def _retry_after_seconds(resp: httpx.Response) -> float:
    """Best-effort extraction of the provider's requested retry delay
    from a 429 — `Retry-After` header first, then the body's
    `error.metadata.retry_after_seconds[_raw]` (OpenRouter shape).
    Falls back to a small constant. Always clamped to the cap."""
    raw = resp.headers.get("retry-after")
    delay: float | None = None
    if raw:
        try:
            delay = float(raw)
        except ValueError:
            delay = None
    if delay is None:
        try:
            body = resp.json()
            err = body.get("error", {}) if isinstance(body, dict) else {}
            meta = err.get("metadata", {}) if isinstance(err, dict) else {}
            for key in ("retry_after_seconds_raw", "retry_after_seconds"):
                val = meta.get(key)
                if isinstance(val, (int, float)):
                    delay = float(val)
                    break
        except (ValueError, AttributeError):
            delay = None
    if delay is None or delay <= 0:
        delay = _RETRY_429_DEFAULT_SECONDS
    return min(delay, _RETRY_429_CAP_SECONDS)


class LLMGatewayClient:
    """HTTP client for the C8 LiteLLM proxy.

    Every call propagates the mandatory headers `X-Agent-Name` and
    `X-Session-Id` (R-800-013). Optional headers (`X-Phase`,
    `X-Sub-Agent-Id`, `X-Cache-Hint`) are provided as kwargs so callers
    opt in explicitly. The bearer token is either injected at construction
    or per-call; keeping it per-call accommodates user-scoped JWT forwarding.
    """

    def __init__(
        self,
        settings: ClientSettings,
        *,
        bearer_token: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        agent_routes: dict[str, str] | None = None,
    ) -> None:
        self._settings = settings
        # Single shared gateway credential (R-800-012) : an explicit
        # constructor `bearer_token` (e.g. user-scoped JWT forwarding)
        # wins ; otherwise the client uses `C8_GATEWAY_API_KEY` from
        # settings. Stays None when neither is set, so `_auth_headers`
        # still REJECTS a call with no bearer (R-800-012) — callers opt
        # into the unauthenticated mock/Ollama path explicitly via
        # `ClientSettings.effective_bearer` ("no-auth" placeholder).
        self._default_bearer = bearer_token or settings.gateway_api_key or None
        # Client-side per-agent route map (R-800-030 v1 note). Three
        # sources, evaluated in priority order : constructor arg
        # `agent_routes` ; inline JSON from `C8_AGENT_ROUTES_INLINE` ;
        # `agent_routes:` block of the YAML at `C8_AGENT_ROUTES_YAML_PATH`.
        # First non-empty wins ; absence is fine (the client then falls
        # back to `default_model` for every agent).
        self._agent_routes: dict[str, str] = (
            agent_routes if agent_routes is not None else _load_agent_routes(settings)
        )
        # Reuse caller-provided client (e.g. from FastAPI app state) so that
        # connection pooling is shared. Otherwise spawn a dedicated client
        # and close it via `aclose()`.
        self._owned_client = http_client is None
        self._client = http_client or make_traced_client(
            base_url=settings.gateway_url,
            timeout=httpx.Timeout(
                settings.request_timeout_seconds,
                connect=settings.connect_timeout_seconds,
            ),
        )

    async def aclose(self) -> None:
        if self._owned_client:
            await self._client.aclose()

    # ------------------------------------------------------------------
    # Per-agent route resolution (R-800-030)
    # ------------------------------------------------------------------

    def _resolve_model(
        self, payload: ChatCompletionRequest, agent_name: str,
    ) -> ChatCompletionRequest:
        """Apply the v1 client-side resolver per R-800-030 :
          1. explicit `payload.model` wins ;
          2. `agent_routes[agent_name]` ;
          3. fallback to `settings.default_model`.

        Returns the (possibly model-substituted) payload. No mutation of
        the input — Pydantic's `model_copy` keeps the original safe.
        """
        if payload.model:
            return payload
        target = self._agent_routes.get(agent_name) or self._settings.default_model
        if not target:
            return payload  # let the proxy emit a 400 per R-800-030
        return payload.model_copy(update={"model": target})

    # ------------------------------------------------------------------
    # Chat completions
    # ------------------------------------------------------------------

    async def chat_completion(
        self,
        payload: ChatCompletionRequest,
        *,
        agent_name: str,
        session_id: str,
        tenant_id: str | None = None,
        project_id: str | None = None,
        phase: str | None = None,
        sub_agent_id: str | None = None,
        cache_hint: str | None = None,
        bearer_token: str | None = None,
    ) -> ChatCompletionResponse:
        """Non-streaming chat completion.

        `agent_name` and `session_id` are required — their absence at the
        Python layer catches the mistake before the gateway does.
        """
        if payload.stream:
            raise ValueError(
                "chat_completion() does not support streaming payloads; "
                "call chat_completion_stream() instead"
            )
        payload = self._resolve_model(payload, agent_name)
        headers = self._headers(
            agent_name=agent_name,
            session_id=session_id,
            tenant_id=tenant_id,
            project_id=project_id,
            phase=phase,
            sub_agent_id=sub_agent_id,
            cache_hint=cache_hint,
            bearer_token=bearer_token,
        )
        body = payload.model_dump(exclude_none=True)
        last_resp: httpx.Response | None = None
        for attempt in range(_RETRY_429_MAX_ATTEMPTS):
            resp = await self._client.post(
                "/chat/completions", json=body, headers=headers,
            )
            if resp.status_code == 200:
                return ChatCompletionResponse.model_validate(resp.json())
            last_resp = resp
            # Only 429 (provider rate-limit) is retryable ; every
            # other non-200 is a hard error surfaced immediately.
            if resp.status_code != 429 or attempt == _RETRY_429_MAX_ATTEMPTS - 1:
                break
            await asyncio.sleep(_retry_after_seconds(resp))
        assert last_resp is not None
        raise LLMGatewayError(last_resp.status_code, last_resp.text)

    @asynccontextmanager
    async def chat_completion_stream(
        self,
        payload: ChatCompletionRequest,
        *,
        agent_name: str,
        session_id: str,
        tenant_id: str | None = None,
        project_id: str | None = None,
        phase: str | None = None,
        sub_agent_id: str | None = None,
        cache_hint: str | None = None,
        bearer_token: str | None = None,
    ) -> AsyncIterator[AsyncIterator[dict[str, Any]]]:
        """Streaming chat completion — yields OpenAI-style SSE chunks.

        Used as an async context manager to guarantee the underlying
        connection is released even when the caller cancels early.
        """
        stream_payload = payload.model_copy(update={"stream": True})
        # Resolve the model via the v1 client-side per-agent route
        # resolver (R-800-030). Falls back to settings.default_model
        # when no agent route matches ; leaves `model` unset when both
        # are absent (the proxy then surfaces a 400, the prod-correct
        # behaviour ; mock_llm ignores `model` so tests stay green).
        stream_payload = self._resolve_model(stream_payload, agent_name)
        headers = self._headers(
            agent_name=agent_name,
            session_id=session_id,
            tenant_id=tenant_id,
            project_id=project_id,
            phase=phase,
            sub_agent_id=sub_agent_id,
            cache_hint=cache_hint,
            bearer_token=bearer_token,
        )
        req = self._client.build_request(
            "POST",
            "/chat/completions",
            json=stream_payload.model_dump(exclude_none=True),
            headers=headers,
        )
        resp = await self._client.send(req, stream=True)
        try:
            if resp.status_code != 200:
                body = await resp.aread()
                raise LLMGatewayError(resp.status_code, body.decode("utf-8", "replace"))
            yield _sse_event_iterator(resp)
        finally:
            await resp.aclose()

    # ------------------------------------------------------------------
    # Admin surface — cost + budget
    # ------------------------------------------------------------------

    async def cost_summary(
        self,
        *,
        tenant_id: str,
        project_id: str | None = None,
        bearer_token: str | None = None,
    ) -> CostSummary:
        """GET /admin/v1/costs/summary — aggregated cost for a tenant/project."""
        params: dict[str, str] = {"tenant_id": tenant_id}
        if project_id:
            params["project_id"] = project_id
        resp = await self._client.get(
            "/admin/v1/costs/summary",
            params=params,
            headers=self._auth_headers(bearer_token),
        )
        if resp.status_code != 200:
            raise LLMGatewayError(resp.status_code, resp.text)
        return CostSummary.model_validate(resp.json())

    async def budget_status(
        self,
        *,
        tenant_id: str,
        project_id: str | None = None,
        bearer_token: str | None = None,
    ) -> BudgetStatus:
        """GET /admin/v1/budgets — current consumption vs cap."""
        params: dict[str, str] = {"tenant_id": tenant_id}
        if project_id:
            params["project_id"] = project_id
        resp = await self._client.get(
            "/admin/v1/budgets",
            params=params,
            headers=self._auth_headers(bearer_token),
        )
        if resp.status_code != 200:
            raise LLMGatewayError(resp.status_code, resp.text)
        return BudgetStatus.model_validate(resp.json())

    # ------------------------------------------------------------------
    # Header assembly
    # ------------------------------------------------------------------

    def _headers(
        self,
        *,
        agent_name: str,
        session_id: str,
        tenant_id: str | None,
        project_id: str | None,
        phase: str | None,
        sub_agent_id: str | None,
        cache_hint: str | None,
        bearer_token: str | None,
    ) -> dict[str, str]:
        if not agent_name:
            raise ValueError("X-Agent-Name is mandatory (R-800-013)")
        if not session_id:
            raise ValueError("X-Session-Id is mandatory (R-800-013)")
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "X-Agent-Name": agent_name,
            "X-Session-Id": session_id,
        }
        headers.update(self._auth_headers(bearer_token))
        if tenant_id:
            headers["X-Tenant-Id"] = tenant_id
        if project_id:
            headers["X-Project-Id"] = project_id
        if phase:
            headers["X-Phase"] = phase
        if sub_agent_id:
            headers["X-Sub-Agent-Id"] = sub_agent_id
        if cache_hint:
            if cache_hint not in {"static", "dynamic", "none"}:
                raise ValueError(
                    f"X-Cache-Hint must be static|dynamic|none, got {cache_hint!r}"
                )
            headers["X-Cache-Hint"] = cache_hint
        return headers

    def _auth_headers(self, bearer_token: str | None) -> dict[str, str]:
        token = bearer_token or self._default_bearer
        if not token:
            raise ValueError("bearer token is required (R-800-012)")
        return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# SSE decoding helper — translates OpenAI-compatible SSE to dict chunks
# ---------------------------------------------------------------------------


async def _sse_event_iterator(
    response: httpx.Response,
) -> AsyncIterator[dict[str, Any]]:
    """Parse a streaming response in OpenAI SSE format.

    Emits one dict per `data:` event. Terminates on the `[DONE]` sentinel.
    Comments (heartbeat lines) and empty lines are skipped.
    """
    async for raw_line in response.aiter_lines():
        line = raw_line.strip()
        if not line or line.startswith(":"):  # comment / heartbeat
            continue
        if not line.startswith("data:"):
            continue
        payload = line[len("data:") :].strip()
        if payload == "[DONE]":
            return
        try:
            yield json.loads(payload)
        except json.JSONDecodeError as exc:
            raise LLMGatewayError(200, f"malformed SSE chunk: {payload!r}") from exc
