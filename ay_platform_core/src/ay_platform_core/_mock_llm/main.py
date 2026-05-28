# =============================================================================
# File: main.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/_mock_llm/main.py
# Description: Scripted mock of the C8 LiteLLM proxy. Exposes:
#                POST /v1/chat/completions — OpenAI-compatible endpoint that
#                  returns the next queued envelope. If the queue is empty,
#                  returns a BLOCKED completion (C4 recognises this).
#                POST /admin/enqueue — push a canned completion envelope.
#                POST /admin/reset — clear the queue + call log.
#                GET  /admin/calls — the call log (for test assertions).
#                GET  /health — liveness.
#
#              The service is stateful in-process and single-replica. It is
#              NOT a platform component; it exists purely to drive C4 / C9
#              through realistic flows during system tests without a real
#              LLM provider or cost. The dev stack (e2e_stack.sh dev) SHALL
#              NOT depend on mock_llm — it routes C8 calls to real Ollama
#              via .env.dev (cf. docker-compose.dev.override.yml).
#
# @relation implements:R-100-116
# =============================================================================

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

from ay_platform_core.observability import (
    TraceContextMiddleware,
    configure_logging,
)
from ay_platform_core.observability.config import LoggingSettings


class EnqueueRequest(BaseModel):
    """Admin endpoint payload.

    The envelope is serialised by the mock into the assistant message content,
    matching how real LiteLLM wraps tool-shaped outputs.
    """

    envelope: dict[str, Any]


def create_app() -> FastAPI:
    log_cfg = LoggingSettings()
    configure_logging(component="_mock_llm", settings=log_cfg)
    app = FastAPI(title="Mock LLM (C8 stand-in)")
    app.add_middleware(TraceContextMiddleware, sample_rate=log_cfg.trace_sample_rate)

    responses: list[dict[str, Any]] = []
    calls_seen: list[dict[str, Any]] = []

    @app.post("/admin/enqueue")
    async def enqueue(payload: EnqueueRequest) -> dict[str, int]:
        responses.append(payload.envelope)
        return {"queued": len(responses)}

    @app.post("/admin/reset")
    async def reset() -> dict[str, str]:
        responses.clear()
        calls_seen.clear()
        return {"status": "reset"}

    @app.get("/admin/calls")
    async def list_calls() -> list[dict[str, Any]]:
        return list(calls_seen)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "component": "_mock_llm"}

    @app.post("/v1/chat/completions", response_model=None)
    async def completions(
        request: Request,
        x_agent_name: str | None = Header(default=None),
        x_session_id: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="bearer required")
        if not x_agent_name or not x_session_id:
            raise HTTPException(status_code=400, detail="missing X-Agent-Name or X-Session-Id")
        body = await request.json()
        calls_seen.append(body)
        if responses:
            envelope = responses.pop(0)
        else:
            envelope = {
                "status": "BLOCKED",
                "output": {},
                "blocker": {"reason": "mock LLM queue empty"},
            }
        return {
            "id": f"mock-{len(calls_seen)}",
            "object": "chat.completion",
            "created": 1_700_000_000,
            "model": "mock",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(envelope),
                    },
                    "finish_reason": "stop",
                },
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

    return app


app = create_app()
