# =============================================================================
# File: test_document_tools.py
# Version: 2
# Path: ay_platform_core/tests/unit/c3_conversation/test_document_tools.py
# Description: Unit tests for the chat-direct DocGen tool layer
#              (D-015 / Phase 2.C.2) :
#                - `parse_tool_calls` : robust extraction of tool_calls
#                  from a non-streaming C8 ChatMessage (dict args,
#                  JSON-string args, malformed args).
#                - The `_run_tool_loop` flow on `ConversationService`
#                  with a fake LLM + fake DocumentToolClient : a
#                  tool-call round is executed, results fed back, and
#                  the final plain-text answer emitted over SSE.
#
#              v2 (2026-05-21): `DocumentToolClient` forwards the
#              optional `turn_id` as the `X-Turn-Id` header on every
#              mutation call (live-docs per-response version batching,
#              R-200-147). Verified end-to-end through an httpx
#              MockTransport that captures the outbound request.
# =============================================================================

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import httpx
import pytest

from ay_platform_core.c3_conversation.document_tools import (
    DocumentToolClient,
    parse_tool_calls,
)
from ay_platform_core.c3_conversation.service import ConversationService
from ay_platform_core.c8_llm.models import (
    ChatCompletionResponse,
    ChatMessage,
    ChatRole,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# parse_tool_calls
# ---------------------------------------------------------------------------


def _msg_with_tool_calls(raw: Any) -> ChatMessage:
    return ChatMessage.model_validate(
        {"role": ChatRole.ASSISTANT, "content": "", "tool_calls": raw},
    )


class TestParseToolCalls:
    def test_no_tool_calls_returns_empty(self) -> None:
        msg = ChatMessage(role=ChatRole.ASSISTANT, content="just text")
        assert parse_tool_calls(msg) == []

    def test_dict_arguments(self) -> None:
        msg = _msg_with_tool_calls(
            [
                {
                    "id": "c1",
                    "function": {
                        "name": "create_document",
                        "arguments": {"path": "a.md", "content": "x"},
                    },
                },
            ],
        )
        out = parse_tool_calls(msg)
        assert out == [
            {
                "id": "c1",
                "name": "create_document",
                "arguments": {"path": "a.md", "content": "x"},
            },
        ]

    def test_json_string_arguments(self) -> None:
        """OpenAI-style: arguments is a JSON string, not a dict."""
        msg = _msg_with_tool_calls(
            [
                {
                    "id": "c2",
                    "function": {
                        "name": "read_document",
                        "arguments": json.dumps({"path": "docs/x.md"}),
                    },
                },
            ],
        )
        out = parse_tool_calls(msg)
        assert out[0]["arguments"] == {"path": "docs/x.md"}

    def test_malformed_json_arguments_degrade_to_empty(self) -> None:
        msg = _msg_with_tool_calls(
            [
                {
                    "id": "c3",
                    "function": {"name": "list_documents", "arguments": "{not json"},
                },
            ],
        )
        out = parse_tool_calls(msg)
        assert out[0]["arguments"] == {}
        assert out[0]["name"] == "list_documents"

    def test_missing_id_synthesised(self) -> None:
        msg = _msg_with_tool_calls(
            [{"function": {"name": "list_documents", "arguments": "{}"}}],
        )
        out = parse_tool_calls(msg)
        assert out[0]["id"].startswith("call_")

    def test_non_list_tool_calls_ignored(self) -> None:
        msg = _msg_with_tool_calls("not a list")
        assert parse_tool_calls(msg) == []

    def test_inline_tool_call_tag_in_content(self) -> None:
        """qwen2.5:3b 2026-05-18 incident : the tool call comes back as
        TEXT in message.content wrapped in <tool_call> tags, NOT in the
        structured field. With nested `arguments` braces + a JSON-string
        body. The brace-balanced + lenient parse SHALL recover it."""
        content = (
            "Sure, here you go:\n"
            "<tool_call>\n"
            '{"name": "create_document", "arguments": '
            '{"path": "docs/plan-q3.md", "content": "# Plan\\n\\n## A"}}\n'
            "</tool_call>"
        )
        msg = ChatMessage(role=ChatRole.ASSISTANT, content=content)
        out = parse_tool_calls(msg)
        assert len(out) == 1
        assert out[0]["name"] == "create_document"
        assert out[0]["arguments"]["path"] == "docs/plan-q3.md"
        assert out[0]["arguments"]["content"].startswith("# Plan")

    def test_inline_tool_call_with_raw_newlines_and_backslash_path(self) -> None:
        """The exact malformed shape observed : literal newlines inside
        the `content` string value AND a Windows-backslash path. Lenient
        parse (`strict=False` + backslash repair) SHALL still extract
        the call (path normalised, leading-slash left for C4 to 400)."""
        content = (
            "<tool_call>\n"
            '{"name": "create_document", "arguments": '
            '{"content": "# Plan\nLine two\nLine three", '
            '"path": "\\docs\\plan-q3.md"}}\n'
            "</tool_call>"
        )
        msg = ChatMessage(role=ChatRole.ASSISTANT, content=content)
        out = parse_tool_calls(msg)
        assert len(out) == 1
        assert out[0]["name"] == "create_document"
        # Backslashes collapsed to forward slashes by the repair pass.
        assert "\\" not in out[0]["arguments"]["path"]
        assert "Line two" in out[0]["arguments"]["content"]

    def test_inline_bare_json_no_tags(self) -> None:
        """Some models drop the tags and emit just the JSON object."""
        content = '{"name": "list_documents", "arguments": {}}'
        msg = ChatMessage(role=ChatRole.ASSISTANT, content=content)
        out = parse_tool_calls(msg)
        assert out == [
            {"id": "inline_0", "name": "list_documents", "arguments": {}},
        ]

    def test_inline_tool_call_double_comma_qwen_phase_2c3(self) -> None:
        """REGRESSION (c3 logs 2026-05-18, Phase 2.C.3). qwen2.5:3b
        emitted the CORRECT update_document call but with a literal
        double comma between keys and a stray leading comma :

          <tool_call>
          ,{"name": "update_document",,"arguments": {...}}
          </tool_call>

        `json.loads(strict=False)` rejects `,,` so the call was
        dropped and the raw text echoed to the user as the answer
        (document never updated). The comma-repair pass SHALL recover
        it. This is the exact byte sequence from the incident log."""
        content = (
            "<tool_call>\n"
            ',{"name": "update_document",,"arguments": '
            '{"path": "docs/test1.md", "content": '
            '"# Intro\\n\\nThis is a brief introduction.'
            '\\n\\n## Budget\\n\\nThis section will provide details '
            'about the budget plan."}}\n'
            "</tool_call>"
        )
        msg = ChatMessage(role=ChatRole.ASSISTANT, content=content)
        out = parse_tool_calls(msg)
        assert len(out) == 1
        assert out[0]["name"] == "update_document"
        assert out[0]["arguments"]["path"] == "docs/test1.md"
        assert "## Budget" in out[0]["arguments"]["content"]

    def test_comma_repair_preserves_commas_inside_strings(self) -> None:
        """The comma repair MUST be string-literal-aware : a `content`
        value containing real prose commas (or even a literal `,,`
        typed by the user) SHALL pass through untouched. Only
        structural commas outside strings are normalised."""
        content = (
            "<tool_call>\n"
            '{"name": "create_document",,"arguments": '
            '{"path": "docs/n.md", "content": '
            '"One, two,, and three. Final, clause."}}\n'
            "</tool_call>"
        )
        msg = ChatMessage(role=ChatRole.ASSISTANT, content=content)
        out = parse_tool_calls(msg)
        assert len(out) == 1
        assert out[0]["name"] == "create_document"
        # The structural `,,` between keys was repaired, but the prose
        # `two,,` inside the string value is preserved verbatim.
        assert out[0]["arguments"]["content"] == "One, two,, and three. Final, clause."

    def test_plain_text_answer_yields_no_calls(self) -> None:
        """A genuine prose answer with stray braces in it MUST NOT be
        misread as a tool call (no name/arguments shape)."""
        msg = ChatMessage(
            role=ChatRole.ASSISTANT,
            content="The config uses a JSON block like {a: 1} — see docs.",
        )
        assert parse_tool_calls(msg) == []


# ---------------------------------------------------------------------------
# _run_tool_loop flow
# ---------------------------------------------------------------------------


class _FakeLLM:
    """Returns a scripted sequence of ChatCompletionResponse objects,
    one per `chat_completion` call (the tool loop calls it once per
    round)."""

    def __init__(self, scripted: list[dict[str, Any]]) -> None:
        self._scripted = scripted
        self.calls: list[Any] = []

    async def chat_completion(self, payload: Any, **kwargs: Any) -> Any:
        self.calls.append(payload)
        envelope = self._scripted.pop(0)
        return ChatCompletionResponse.model_validate(
            {
                "id": f"resp-{len(self.calls)}",
                "object": "chat.completion",
                "created": 1_700_000_000,
                "model": "fake",
                "choices": [
                    {"index": 0, "message": envelope, "finish_reason": "stop"},
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )


class _FakeDocTools:
    def __init__(self) -> None:
        self.executed: list[tuple[str, dict[str, Any]]] = []

    async def execute(
        self, *, name: str, arguments: dict[str, Any], **_: Any,
    ) -> dict[str, Any]:
        self.executed.append((name, arguments))
        if name == "create_document":
            return {"created": {"path": arguments["path"], "size_bytes": 5}}
        return {"documents": []}


@pytest.mark.asyncio
async def test_tool_loop_executes_then_answers() -> None:
    """Round 1 : model asks to create a doc → tool executed. Round 2 :
    model answers in plain text → emitted as the final SSE chunk and
    appended to collected_tokens (so the caller persists it)."""
    fake_llm = _FakeLLM(
        [
            # Round 1 — tool call.
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "tc1",
                        "function": {
                            "name": "create_document",
                            "arguments": json.dumps(
                                {"path": "docs/spec.md", "content": "# Spec"},
                            ),
                        },
                    },
                ],
            },
            # Round 2 — final text answer.
            {"role": "assistant", "content": "Done — I created docs/spec.md."},
        ],
    )
    fake_tools = _FakeDocTools()
    svc = ConversationService(
        repo=object(),  # type: ignore[arg-type]  # not touched by _run_tool_loop
        llm_client=fake_llm,  # type: ignore[arg-type]
        document_tools=fake_tools,  # type: ignore[arg-type]
    )

    messages: list[ChatMessage] = [
        ChatMessage(role=ChatRole.SYSTEM, content="sys"),
        ChatMessage(role=ChatRole.USER, content="write a spec doc"),
    ]
    collected: list[str] = []
    # Unified contract (2026-05-19) : tool events travel the single
    # `event: inline` channel (kind='tool_call') and terminal events
    # land in `collected_events` — the shared audit ledger persisted
    # as MessagePublic.events. `_run_tool_loop` now requires it.
    events: list[dict[str, object]] = []
    sse_chunks: list[str] = []
    async for sse in svc._run_tool_loop(
        messages=messages,
        conversation_id=uuid4(),
        project_id="proj-x",
        tenant_id="tenant-x",
        user_id="alice",
        user_roles="project_editor",
        collected_tokens=collected,
        collected_events=events,
    ):
        sse_chunks.append(sse)

    # The tool was executed exactly once with the parsed args.
    assert fake_tools.executed == [
        ("create_document", {"path": "docs/spec.md", "content": "# Spec"}),
    ]
    # Final answer captured for persistence.
    assert "".join(collected).strip() == "Done — I created docs/spec.md."
    # SSE stream carried unified inline events (running + done) and
    # the final data chunk. Was: `event: tool_call` + `"phase":...` ;
    # now: one `event: inline` channel discriminated by `kind`, with
    # a `status` running/done — the unified-pipeline contract.
    joined = "".join(sse_chunks)
    assert "event: inline" in joined
    assert "event: tool_call" not in joined
    assert '"kind":"tool_call"' in joined
    assert '"status":"running"' in joined
    assert '"status":"done"' in joined
    assert "data: Done — I created docs/spec.md.\n\n" in joined
    # The terminal tool event was recorded in the audit ledger.
    assert [e["kind"] for e in events] == ["tool_call"]
    assert events[0]["status"] == "done"
    assert events[0]["name"] == "create_document"
    assert events[0]["ok"] is True


@pytest.mark.asyncio
async def test_tool_loop_plain_answer_no_tools() -> None:
    """Model answers directly without any tool call → single SSE data
    chunk, no tool execution."""
    fake_llm = _FakeLLM(
        [{"role": "assistant", "content": "No document needed for that."}],
    )
    fake_tools = _FakeDocTools()
    svc = ConversationService(
        repo=object(),  # type: ignore[arg-type]
        llm_client=fake_llm,  # type: ignore[arg-type]
        document_tools=fake_tools,  # type: ignore[arg-type]
    )
    collected: list[str] = []
    events: list[dict[str, object]] = []
    chunks: list[str] = []
    async for sse in svc._run_tool_loop(
        messages=[ChatMessage(role=ChatRole.USER, content="hi")],
        conversation_id=uuid4(),
        project_id="p",
        tenant_id="t",
        user_id="u",
        user_roles="project_viewer",
        collected_tokens=collected,
        collected_events=events,
    ):
        chunks.append(sse)

    assert fake_tools.executed == []
    assert "".join(collected).strip() == "No document needed for that."
    # No tool ran → no inline event emitted by the loop and the audit
    # ledger stays empty (unified-channel contract).
    assert "event: inline" not in "".join(chunks)
    assert events == []


@pytest.mark.asyncio
async def test_tool_loop_round_budget_exhausted() -> None:
    """Model keeps calling tools forever → loop stops at the budget
    and emits a graceful message instead of looping unbounded."""
    # Always return a tool call ; the loop must bail after
    # max_tool_rounds.
    tool_call_envelope = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "x",
                "function": {"name": "list_documents", "arguments": "{}"},
            },
        ],
    }
    fake_llm = _FakeLLM([dict(tool_call_envelope) for _ in range(10)])
    svc = ConversationService(
        repo=object(),  # type: ignore[arg-type]
        llm_client=fake_llm,  # type: ignore[arg-type]
        document_tools=_FakeDocTools(),  # type: ignore[arg-type]
        max_tool_rounds=3,
    )
    collected: list[str] = []
    events: list[dict[str, object]] = []
    chunks: list[str] = []
    async for sse in svc._run_tool_loop(
        messages=[ChatMessage(role=ChatRole.USER, content="loop")],
        conversation_id=uuid4(),
        project_id="p",
        tenant_id="t",
        user_id="u",
        user_roles="project_editor",
        collected_tokens=collected,
        collected_events=events,
    ):
        chunks.append(sse)

    # Exactly max_tool_rounds LLM calls, then the budget message.
    assert len(fake_llm.calls) == 3
    assert "tool-call budget" in "".join(collected)
    # list_documents ran each round → 3 terminal tool events recorded
    # in the unified audit ledger.
    assert [e["kind"] for e in events] == ["tool_call", "tool_call", "tool_call"]


# ---------------------------------------------------------------------------
# X-Turn-Id forwarding (live-docs per-response version batching)
# ---------------------------------------------------------------------------


def _client_with_capture(
    captured: list[httpx.Request],
) -> DocumentToolClient:
    """A DocumentToolClient whose transport records every outbound
    request and replies with a minimal success body, so a unit test can
    assert on the headers C3 sends to C4 without a live C4."""

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.method == "POST":
            return httpx.Response(201, json={"path": "a.md", "size_bytes": 1})
        if request.method == "PUT":
            return httpx.Response(200, json={"path": "a.md", "size_bytes": 2})
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(200, json={"documents": []})

    client = DocumentToolClient("http://c4")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    return client


@pytest.mark.asyncio
async def test_execute_forwards_turn_id_header_on_create() -> None:
    """A create_document call carrying a turn id MUST send it as the
    `X-Turn-Id` header so C4 can tag the commit and batch the version."""
    captured: list[httpx.Request] = []
    client = _client_with_capture(captured)
    try:
        await client.execute(
            name="create_document",
            arguments={"path": "a.md", "content": "x"},
            project_id="p",
            user_id="alice",
            tenant_id="t",
            user_roles="project_editor",
            turn_id="turn-xyz",
        )
    finally:
        await client.aclose()
    assert len(captured) == 1
    assert captured[0].headers.get("X-Turn-Id") == "turn-xyz"
    # The identity headers travel alongside, unchanged.
    assert captured[0].headers.get("X-User-Id") == "alice"


@pytest.mark.asyncio
async def test_execute_omits_turn_id_header_when_absent() -> None:
    """No turn id (operator-driven path / not in a chat loop) → the
    header is simply not sent, so C4 records an untagged commit."""
    captured: list[httpx.Request] = []
    client = _client_with_capture(captured)
    try:
        await client.execute(
            name="update_document",
            arguments={"path": "a.md", "content": "y"},
            project_id="p",
            user_id="alice",
            tenant_id="t",
            user_roles="project_editor",
        )
    finally:
        await client.aclose()
    assert len(captured) == 1
    assert "X-Turn-Id" not in captured[0].headers
