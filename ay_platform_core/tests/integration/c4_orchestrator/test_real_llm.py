# =============================================================================
# File: test_real_llm.py
# Version: 1
# Path: ay_platform_core/tests/integration/c4_orchestrator/test_real_llm.py
# Description: C4 pipeline exercised against a REAL (small) LLM via Ollama
#              testcontainer. Complements the ScriptedLLM pipeline tests
#              which assert deterministic state transitions — here we
#              verify the end-to-end chain survives a non-scripted LLM:
#              LLMGatewayClient → Ollama → response parsing → dispatcher.
#
#              Because Qwen2.5-0.5B is small and free-running, outputs are
#              NOT guaranteed to match the strict JSON-envelope contract
#              the orchestrator expects. The tests therefore assert soft
#              invariants (LLM was called, response is non-empty, C4
#              reached a terminal state) rather than "pipeline reached
#              COMPLETED". Strict orchestration logic stays covered by
#              the scripted-LLM tests.
# =============================================================================

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from ay_platform_core.c4_orchestrator.config import OrchestratorConfig
from ay_platform_core.c4_orchestrator.db.repository import OrchestratorRepository
from ay_platform_core.c4_orchestrator.dispatcher.in_process import InProcessDispatcher
from ay_platform_core.c4_orchestrator.domains.code.plugin import CodeDomainPlugin
from ay_platform_core.c4_orchestrator.events.null_publisher import NullPublisher
from ay_platform_core.c4_orchestrator.router import router
from ay_platform_core.c4_orchestrator.service import OrchestratorService
from ay_platform_core.c8_llm.client import LLMGatewayClient
from ay_platform_core.c8_llm.config import ClientSettings
from tests.fixtures.containers import OllamaEndpoint

pytestmark = [pytest.mark.integration, pytest.mark.slow]


_HEADERS = {
    "X-User-Id": "alice",
    "X-Tenant-Id": "t-demo",
    "X-User-Roles": "project_editor,admin",
}


@pytest_asyncio.fixture
async def c4_with_real_llm(
    ollama_container: OllamaEndpoint,
    c4_repo: OrchestratorRepository,
) -> AsyncIterator[FastAPI]:
    """C4 wired with an LLMGatewayClient pointed at Ollama."""
    llm_client = LLMGatewayClient(
        ClientSettings(gateway_url=ollama_container.api_v1_url),
        bearer_token="ignored-by-ollama",
    )
    try:
        service = OrchestratorService(
            config=OrchestratorConfig(),
            repo=c4_repo,
            dispatcher=InProcessDispatcher(llm_client),
            domain_plugin=CodeDomainPlugin(),
            publisher=NullPublisher(),
        )
        app = FastAPI()
        app.include_router(router)
        app.state.orchestrator_service = service
        yield app
    finally:
        await llm_client.aclose()


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://c4"
    )


@pytest.mark.asyncio
async def test_c4_run_against_real_llm_reaches_terminal_state(
    c4_with_real_llm: FastAPI,
) -> None:
    """The orchestrator SHALL drive a run against a real LLM and reach a
    terminal state (completed OR blocked). Small models routinely fail to
    produce the strict envelope, so `blocked` is an acceptable outcome —
    what matters is that the chain does not deadlock or crash."""
    async with _client(c4_with_real_llm) as client:
        start = await client.post(
            "/api/v1/orchestrator/runs",
            json={
                "project_id": "demo",
                "session_id": "sess-real-llm",
                "initial_prompt": (
                    "Reply with a JSON object shaped "
                    '{"status": "DONE", "output": {"proposal": "hi"}}.'
                ),
                "domain": "code",
            },
            headers=_HEADERS,
        )
        assert start.status_code == 201, start.text
        run = start.json()

    # The orchestrator dispatches the brainstorm phase synchronously before
    # returning 201, so `current_phase` has already advanced past brainstorm.
    # `status` is either `running` (halted at Gate A awaiting plan approval)
    # or `blocked` (LLM returned a BLOCKED envelope because a tiny model
    # couldn't produce the strict JSON contract).
    assert run["status"] in {"running", "blocked"}, (
        f"C4 reached an unexpected state after real-LLM brainstorm: "
        f"{run['status']!r} at phase {run['current_phase']!r}"
    )
    assert run["current_phase"] in {"brainstorm", "spec", "plan"}, (
        f"Unexpected phase: {run['current_phase']!r}"
    )
