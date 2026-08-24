# =============================================================================
# File: client.py
# Version: 6
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
# @relation implements:R-800-147
# @relation implements:R-800-013
# @relation implements:R-800-014
# @relation implements:R-800-030
# @relation implements:R-800-073
# =============================================================================

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
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
from ay_platform_core.c8_llm.registry.key_provider import CallTarget
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


# Wire formats whose request shape keys the prompt cache on an EXPLICIT
# breakpoint marker. Providers with automatic server-side caching (OpenAI,
# Gemini, …) are deliberately absent: they need no marker, AND an
# Anthropic-shaped ``cache_control`` block sent to them is REJECTED. The cache
# decision is thus translated per provider at the C8 gateway (spec R-800-042).
_CACHE_MARKER_WIRE_FORMATS = frozenset({"anthropic"})


def _apply_adaptive_thinking(body: dict[str, Any]) -> None:
    """Request adaptive extended thinking (R-800-147) — set on the request when
    the caller asked for VERBOSE reasoning (R-200-207). LiteLLM passes `thinking`
    through to the provider; on Claude 4.6+ tiers this yields streamed thinking
    blocks (surfaced downstream as `reasoning` events). `mock_llm` ignores it, so
    tests stay green. No-op if the caller already set `thinking`."""
    body.setdefault("thinking", {"type": "adaptive"})


def _apply_static_prompt_cache(body: dict[str, Any]) -> None:
    """Mark the (stable) system prompt as a prompt-cache breakpoint — PROVIDER
    AWARE.

    Invoked AFTER upstream resolution (when ``cache_hint="static"``), so
    ``body['model']`` is ``<wire_format>/<upstream>``. Only wire formats that
    require an explicit marker (Anthropic) get ``cache_control: {type:
    ephemeral}`` on the last system message ; for every other provider — or an
    unresolved alias / mock (no ``/`` in the model) — this is a no-op, because
    they either cache automatically or would reject the Anthropic-shaped block.

    For a marker-using provider, below the model's minimum cacheable size
    (Haiku 4096 / Sonnet 1024 tokens) the API silently skips caching — a safe
    no-op — so callers can always opt in ; it only ever helps when a large
    stable prefix (long system prompt + shared context) recurs across calls in
    the cache window (e.g. C3 chat turns). Mutates ``body`` in place."""
    model = body.get("model")
    if not isinstance(model, str) or "/" not in model:
        return  # unresolved alias / mock — provider unknown, never guess
    if model.split("/", 1)[0] not in _CACHE_MARKER_WIRE_FORMATS:
        return  # automatic-caching provider — emitting a marker would error
    messages = body.get("messages")
    if not isinstance(messages, list):
        return
    sys_idx: int | None = None
    for i, m in enumerate(messages):
        if isinstance(m, dict) and m.get("role") == "system":
            sys_idx = i  # keep the LAST system message — the stable prefix end
    if sys_idx is None:
        return
    msg = messages[sys_idx]
    content = msg.get("content")
    if isinstance(content, str):
        msg["content"] = [
            {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
        ]
    elif isinstance(content, list) and content and isinstance(content[-1], dict):
        content[-1].setdefault("cache_control", {"type": "ephemeral"})


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
        key_provider: (
            Callable[[str], Awaitable[CallTarget | None]] | None
        ) = None,
        model_provider: Callable[..., Awaitable[str | None]] | None = None,
        quota_guard: Callable[..., Awaitable[None]] | None = None,
    ) -> None:
        self._settings = settings
        # LLM-governance Lot 3 : optional per-call quota guard. Given the call's
        # tenant_id it enforces the global QuotaPolicy — best-effort warn, hard
        # raise (QuotaExceededError) on an exceeded window, which stops the call
        # BEFORE any upstream spend. None / no tenant_id → no enforcement.
        self._quota_guard = quota_guard
        # LLM-governance #3 (option B) : optional per-request upstream-override
        # injector. Given the RESOLVED model name it returns an
        # `UpstreamConnection` (decrypted registry `api_key` + optional
        # `api_base`), injected into the request body to override the proxy's
        # defaults for that call (litellm `configurable_clientside_auth_params`).
        # None → no override (proxy env fallback). Best-effort : a provider
        # returning None or raising leaves the call on the fallback.
        self._key_provider = key_provider
        # Provider-INDEPENDENT model resolution (D-011). When a call arrives with
        # no explicit model AND no client-side route, this resolves the agent →
        # a CONCRETE model alias from the operator's registry catalogue, scoped
        # to the call's project. So registering + associating a model in the HMI
        # is enough — NO model/provider name in config. None → the legacy
        # agent_routes / default_model path only. Best-effort (None on failure).
        self._model_provider = model_provider
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

    async def _resolve_model_alias(
        self,
        body: dict[str, Any],
        *,
        agent_name: str,
        tenant_id: str | None,
        project_id: str | None,
    ) -> None:
        """Provider-INDEPENDENT model selection (D-011). When no model was
        chosen (no explicit `model`, no client-side agent route), resolve one
        from the operator's registry catalogue — scoped to the call's project,
        by the agent's quality tier — and set ``body['model']``. So registering
        + associating a model in the HMI is sufficient; NO model/provider name
        lives in config. Best-effort: `None` leaves the body unset (the proxy
        then 400s with a clear 'no model' error rather than a silent default).
        Runs BEFORE `_inject_upstream_key`, which rewrites the alias to the
        provider endpoint + key."""
        if self._model_provider is None or body.get("model"):
            return
        try:
            alias = await self._model_provider(
                agent_name,
                tenant_id,
                project_id,
                require_tool_calling=bool(body.get("tools")),
            )
        except Exception:  # best-effort, never break a call
            return
        if alias:
            body["model"] = alias

    async def _inject_upstream_key(self, body: dict[str, Any]) -> None:
        """Resolve the alias in ``body['model']`` to its registry CallTarget and
        REWRITE the request: ``model`` → ``<provider.wire_format>/<upstream>``,
        plus the provider's mandatory ``api_base`` and (when stored) ``api_key``.
        Routing thus depends on the provider's explicit endpoint, never a
        built-in default.

        Best-effort and SILENT on any failure: an unknown alias (e.g. the test
        mock model), a dangling provider, or a resolver error all leave the body
        untouched so the proxy/mock handles the original model. The plaintext
        key is set on the body but is NEVER logged (the client logs no bodies)."""
        if self._key_provider is None:
            return
        model = body.get("model")
        if not isinstance(model, str) or not model:
            return
        try:
            target = await self._key_provider(model)
        except Exception:
            # Resolver is best-effort; a registry/decrypt failure must NEVER
            # break an LLM call — fall back to the original model + proxy env.
            return
        if target is None:
            return
        body["model"] = target.model
        body["api_base"] = target.api_base
        if target.api_key:
            body["api_key"] = target.api_key

    async def _enforce_quota(
        self,
        tenant_id: str | None,
        project_id: str | None = None,
        user_id: str | None = None,
    ) -> None:
        """Run the per-call quota guard BEFORE sending, enforcing every level
        whose subject is known (global always; tenant/project/user when their id
        is supplied). A blocked subject raises `QuotaExceededError` so no upstream
        spend occurs. No guard or no tenant_id → no-op."""
        if self._quota_guard is None or not tenant_id:
            return
        await self._quota_guard(tenant_id, project_id=project_id, user_id=user_id)

    async def check_quota(
        self,
        *,
        tenant_id: str | None = None,
        project_id: str | None = None,
        user_id: str | None = None,
    ) -> None:
        """Run the quota guard WITHOUT sending an LLM call. A STREAMING caller
        invokes this eagerly — before returning its `StreamingResponse` — so a
        hard block raises `QuotaExceededError` (→ 429) BEFORE the SSE stream
        starts (once streaming, the in-flight guard's block can't become a 429)."""
        await self._enforce_quota(tenant_id, project_id, user_id)

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
        user_id: str | None = None,
        phase: str | None = None,
        sub_agent_id: str | None = None,
        cache_hint: str | None = None,
        bearer_token: str | None = None,
        run_id: str | None = None,
        turn_id: str | None = None,
        reasoning_verbose: bool = False,
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
            user_id=user_id,
            phase=phase,
            sub_agent_id=sub_agent_id,
            cache_hint=cache_hint,
            bearer_token=bearer_token,
            run_id=run_id,
            turn_id=turn_id,
        )
        body = payload.model_dump(exclude_none=True)
        if reasoning_verbose:
            _apply_adaptive_thinking(body)
        await self._enforce_quota(tenant_id, project_id=project_id, user_id=user_id)
        await self._resolve_model_alias(
            body, agent_name=agent_name, tenant_id=tenant_id, project_id=project_id
        )
        await self._inject_upstream_key(body)
        # Cache marker AFTER upstream resolution: the marker is provider-aware
        # and reads the now-rewritten `<wire_format>/<upstream>` model.
        if cache_hint == "static":
            _apply_static_prompt_cache(body)
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
        user_id: str | None = None,
        phase: str | None = None,
        sub_agent_id: str | None = None,
        cache_hint: str | None = None,
        bearer_token: str | None = None,
        run_id: str | None = None,
        turn_id: str | None = None,
        reasoning_verbose: bool = False,
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
            user_id=user_id,
            phase=phase,
            sub_agent_id=sub_agent_id,
            cache_hint=cache_hint,
            bearer_token=bearer_token,
            run_id=run_id,
            turn_id=turn_id,
        )
        stream_body = stream_payload.model_dump(exclude_none=True)
        if reasoning_verbose:
            _apply_adaptive_thinking(stream_body)
        await self._enforce_quota(tenant_id, project_id=project_id, user_id=user_id)
        await self._resolve_model_alias(
            stream_body, agent_name=agent_name, tenant_id=tenant_id, project_id=project_id
        )
        await self._inject_upstream_key(stream_body)
        # Cache marker AFTER upstream resolution (provider-aware — see above).
        if cache_hint == "static":
            _apply_static_prompt_cache(stream_body)
        req = self._client.build_request(
            "POST",
            "/chat/completions",
            json=stream_body,
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
        user_id: str | None = None,
        phase: str | None,
        sub_agent_id: str | None,
        cache_hint: str | None,
        bearer_token: str | None,
        run_id: str | None = None,
        turn_id: str | None = None,
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
        if user_id:
            # Attribute the call to the user in the ledger so per-user quota can
            # sum + enforce (the cost tracker reads X-User-Id).
            headers["X-User-Id"] = user_id
        if phase:
            headers["X-Phase"] = phase
        if sub_agent_id:
            headers["X-Sub-Agent-Id"] = sub_agent_id
        # Request correlation for the per-request cost breakdown (R-800-146):
        # run_id (a pipeline run) / turn_id (a chat turn). The cost tracker reads
        # X-Run-Id / X-Turn-Id off the forwarded headers.
        if run_id:
            headers["X-Run-Id"] = run_id
        if turn_id:
            headers["X-Turn-Id"] = turn_id
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
