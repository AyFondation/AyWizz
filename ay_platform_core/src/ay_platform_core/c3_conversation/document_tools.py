# =============================================================================
# File: document_tools.py
# Version: 4
# Path: ay_platform_core/src/ay_platform_core/c3_conversation/document_tools.py
# Description: Document tool catalogue + executor for the chat-direct
#              DocGen path (D-015 / Phase 2.C.2). The C3 conversation
#              hands these OpenAI-format tool definitions to the LLM ;
#              when the model emits `tool_calls`, the executor here
#              translates each into an HTTP call against C4's document
#              CRUD surface (R-200-153..156), forwarding the caller's
#              forward-auth identity so C4's RBAC + tenant scoping hold.
#
#              v4 (2026-05-21): `execute` accepts an optional `turn_id`
#              (the assistant-response id) and forwards it as the
#              `X-Turn-Id` header. C4 embeds it in the live-docs commit
#              message so the tree's per-file version batches by AI
#              response (one bump per turn — D-015 / R-200-147).
#
#              v3 (2026-05-18): `_strip_json_comma_artifacts` — the
#              real Phase 2.C.3 root cause. qwen2.5:3b emits the
#              CORRECT update_document tool call but with malformed
#              commas (`{"name": "update_document",,"arguments":...}`)
#              which `json.loads` rejects even with strict=False, so
#              the inline tool call was dropped and the raw text
#              echoed. The repair is string-literal-aware (prose
#              commas in `content` untouched). Proven by c3 logs.
#
#              v2 (2026-05-18): structured logging on tool dispatch —
#              INFO on resolve, WARNING on an unrecognised tool name
#              (qwen2.5:3b inventing `read`/`update` instead of the
#              `_document` suffixed names is the observed Phase 2.C.3
#              failure ; this surfaces it in c3 logs).
#
#              The tool shapes are deliberately the SAME as the
#              `aywiz_working` MCP catalogue the v2 synthesis-v4
#              pipeline path will expose (D-015 migration property) —
#              `create_document` / `update_document` / `read_document`
#              / `list_documents` / `delete_document`. v1 calls C4 over
#              HTTP from C3 ; v2 calls the same shapes from OpenHands
#              in C15. Prompt + UX stay stable across the migration.
#
# @relation implements:R-200-153
# @relation implements:R-200-156
# =============================================================================

from __future__ import annotations

import json
import logging
import re
from typing import Any, ClassVar

import httpx

_log = logging.getLogger("c3_conversation.document_tools")
"""Observability for tool dispatch. INFO when a tool call resolves to
a handler ; WARNING when the model emitted a name we don't recognise
(the canonical 'qwen invents `read`/`update` instead of
`read_document`/`update_document`' failure — Phase 2.C.3)."""

# OpenAI tool-calling schemas. Kept minimal — descriptions are written
# for the model, not the developer (they ARE the model's only spec of
# what each tool does). Path convention spelled out so small models
# don't invent leading slashes.
_PATH_RULE = (
    "POSIX-relative path, forward slashes only, no leading '/', no '..'. "
    "Example: 'docs/proposal.md'."
)

DOC_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_documents",
            "description": (
                "List every document path that already exists in this "
                "project. Call this before creating a document to avoid "
                "overwriting, and to discover what is available."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_document",
            "description": (
                "Return the full UTF-8 content of one existing document. "
                "Use it before updating so you edit from the real current "
                "text, not from memory."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": _PATH_RULE},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_document",
            "description": (
                "Create a new document (or overwrite an existing one) "
                "with the given full content. Prefer update_document when "
                "the path already exists and you only want to change part "
                "of it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": _PATH_RULE},
                    "content": {
                        "type": "string",
                        "description": "The complete UTF-8 text of the file.",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_document",
            "description": (
                "Overwrite an existing document with new full content. "
                "Read the document first if you need to preserve parts "
                "of it — this replaces the whole file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": _PATH_RULE},
                    "content": {
                        "type": "string",
                        "description": "The complete new UTF-8 text of the file.",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_document",
            "description": (
                "Delete a document. Ask the user for confirmation before "
                "calling this — deletion is not silently reversible from "
                "the chat (git history retains it for audit)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": _PATH_RULE},
                },
                "required": ["path"],
            },
        },
    },
]

_TOOL_NAMES = frozenset(t["function"]["name"] for t in DOC_TOOLS)


class DocumentToolClient:
    """Thin async HTTP client over C4's document CRUD surface
    (R-200-153). One instance per app ; the caller's forward-auth
    identity is passed per-call (not at construction) so a single
    client serves every tenant/user."""

    def __init__(self, c4_base_url: str) -> None:
        # Strip a trailing slash so f-string joins are clean.
        self._base = c4_base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=30.0)

    async def aclose(self) -> None:
        await self._client.aclose()

    def _headers(
        self,
        *,
        user_id: str,
        tenant_id: str,
        user_roles: str,
        turn_id: str | None = None,
    ) -> dict[str, str]:
        headers = {
            "X-User-Id": user_id,
            "X-Tenant-Id": tenant_id,
            "X-User-Roles": user_roles,
        }
        # The assistant-response id lets C4 batch the per-file version
        # by AI response (one bump per turn, even with several writes).
        if turn_id:
            headers["X-Turn-Id"] = turn_id
        return headers

    async def execute(
        self,
        *,
        name: str,
        arguments: dict[str, Any],
        project_id: str,
        user_id: str,
        tenant_id: str,
        user_roles: str,
        turn_id: str | None = None,
    ) -> dict[str, Any]:
        """Run one tool call. Returns a dict the caller serialises into
        the `tool` role message fed back to the LLM. Never raises for a
        functional error (404, 400, …) — those are returned as
        `{"error": "..."}` so the model can recover ; a transport
        failure is also caught and returned as an error so the loop
        keeps going."""
        handler = self._HANDLERS.get(name)
        if handler is None:
            _log.warning(
                "execute UNKNOWN_TOOL name=%r arg_keys=%s known=%s",
                name,
                sorted(arguments.keys()),
                sorted(self._HANDLERS.keys()),
            )
            return {"error": f"unknown tool {name!r}"}
        _log.info(
            "execute tool=%s arg_keys=%s project=%s",
            name,
            sorted(arguments.keys()),
            project_id,
        )
        headers = self._headers(
            user_id=user_id,
            tenant_id=tenant_id,
            user_roles=user_roles,
            turn_id=turn_id,
        )
        base = f"{self._base}/api/v1/projects/{project_id}/documents"
        try:
            result: dict[str, Any] = await handler(self, base, headers, arguments)
            return result
        except httpx.HTTPError as exc:
            return {"error": f"transport error calling C4: {exc}"}

    async def _t_list(
        self, base: str, headers: dict[str, str], _args: dict[str, Any],
    ) -> dict[str, Any]:
        r = await self._client.get(base, headers=headers)
        if r.status_code == 200:
            return {"documents": r.json().get("documents", [])}
        return {"error": f"list failed: HTTP {r.status_code}"}

    async def _t_read(
        self, base: str, headers: dict[str, str], args: dict[str, Any],
    ) -> dict[str, Any]:
        path = str(args.get("path", ""))
        r = await self._client.get(f"{base}/{path}", headers=headers)
        if r.status_code == 200:
            return {"path": path, "content": r.text}
        if r.status_code == 404:
            return {"error": f"document {path!r} not found"}
        return {"error": f"read failed: HTTP {r.status_code}"}

    async def _t_create(
        self, base: str, headers: dict[str, str], args: dict[str, Any],
    ) -> dict[str, Any]:
        path = str(args.get("path", ""))
        r = await self._client.post(
            base,
            headers=headers,
            json={"path": path, "content": str(args.get("content", ""))},
        )
        if r.status_code == 201:
            return {"created": r.json()}
        if r.status_code == 400:
            return {"error": f"invalid path {path!r}"}
        return {"error": f"create failed: HTTP {r.status_code}"}

    async def _t_update(
        self, base: str, headers: dict[str, str], args: dict[str, Any],
    ) -> dict[str, Any]:
        path = str(args.get("path", ""))
        r = await self._client.put(
            f"{base}/{path}",
            headers=headers,
            json={"content": str(args.get("content", ""))},
        )
        if r.status_code == 200:
            return {"updated": r.json()}
        if r.status_code == 400:
            return {"error": f"invalid path {path!r}"}
        return {"error": f"update failed: HTTP {r.status_code}"}

    async def _t_delete(
        self, base: str, headers: dict[str, str], args: dict[str, Any],
    ) -> dict[str, Any]:
        path = str(args.get("path", ""))
        r = await self._client.delete(f"{base}/{path}", headers=headers)
        if r.status_code == 204:
            return {"deleted": path}
        if r.status_code == 404:
            return {"error": f"document {path!r} not found"}
        return {"error": f"delete failed: HTTP {r.status_code}"}

    async def read_document_content(
        self,
        *,
        project_id: str,
        path: str,
        user_id: str,
        tenant_id: str,
        user_roles: str,
    ) -> tuple[int, str]:
        """Fetch a live-docs document's full text content. Returns
        `(status_code, body)` ; on 200 the body is the document text
        UTF-8 decoded, on a 4xx the body is the JSON error string. Used
        by the PromptReference resolver (R-200-181) — distinct from the
        tool surface so its semantics are predictable (no JSON wrapping,
        no `{"error": ...}` wrapping)."""
        headers = self._headers(
            user_id=user_id, tenant_id=tenant_id, user_roles=user_roles,
        )
        url = (
            f"{self._base}/api/v1/projects/{project_id}/documents/{path}"
        )
        r = await self._client.get(url, headers=headers)
        return r.status_code, r.text

    # Name → bound-handler dispatch. Keeps `execute` flat (one lookup,
    # one call) so the per-tool HTTP shaping lives in focused methods.
    _HANDLERS: ClassVar[dict[str, Any]] = {
        "list_documents": _t_list,
        "read_document": _t_read,
        "create_document": _t_create,
        "update_document": _t_update,
        "delete_document": _t_delete,
    }


def _scan_balanced_object(text: str, start: int) -> str | None:
    """Return the slice of `text` from `start` (a `{`) through its
    matching `}`, respecting string literals so braces inside `"..."`
    don't perturb the depth. None if unbalanced. Same primitive the
    C4 dispatcher uses — small models nest `arguments` objects, so a
    non-greedy regex would truncate at the first inner `}`."""
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _last_significant(buf: list[str]) -> str:
    """Last non-whitespace char already emitted (``""`` if none).
    Used by the comma-repair pass to tell a leading comma (preceded by
    an opener) from a separating one."""
    for ch in reversed(buf):
        if ch not in " \t\r\n":
            return ch
    return ""


def _strip_json_comma_artifacts(text: str) -> str:
    """Repair the malformed-comma JSON qwen2.5:3b emits in inline tool
    calls : duplicated commas (``"a",,"b"``), a comma right after an
    opening brace/bracket (``{,``) and a trailing comma before a
    closing one (``,}``). String literals are skipped char-for-char so
    commas inside values (e.g. prose in a ``content`` field) are never
    touched. Observed Phase 2.C.3 failure (c3 logs 2026-05-18) :
    ``{"name": "update_document",,"arguments": {...}}`` — a literal
    double comma between keys that `json.loads` rejects even with
    ``strict=False``."""
    out: list[str] = []
    in_string = False
    escape = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == ",":
            # Swallow this comma plus any following whitespace and
            # extra commas (all OUTSIDE a string). Emit a single comma
            # only if it actually separates two values ; drop it when
            # it's leading (after an opener) or trailing (before a
            # closer) — both are invalid JSON.
            j = i + 1
            while j < n and text[j] in ", \t\r\n":
                j += 1
            nxt = text[j] if j < n else ""
            prev = _last_significant(out)
            if prev not in ("{", "[", "") and nxt not in ("}", "]", ""):
                out.append(",")
            i = j
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _lenient_json_object(text: str) -> dict[str, Any] | None:
    """Parse a JSON object from `text`, tolerating the three failure
    modes qwen2.5:3b produces : (1) raw newlines inside string values
    (handled by `strict=False`) ; (2) Windows-style backslashes in
    path values (`\\docs\\x.md`) ; (3) malformed commas — duplicated
    (`"a",,"b"`) or adjacent to a brace. For (2) we cannot blanket-
    replace `\\` (it would corrupt valid escapes like `\\n`), so we
    only do so when a first strict-ish parse raises on an invalid
    escape — then retry after collapsing backslashes that are NOT part
    of a valid JSON escape into forward slashes. Our document paths
    are always POSIX (R-200-130) so this is safe for this tool
    surface. (3) is repaired by `_strip_json_comma_artifacts` as a
    last resort (string-literal-aware, so prose commas are safe)."""
    try:
        parsed = json.loads(text, strict=False)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    # Repair pass : turn any backslash NOT followed by a valid JSON
    # escape char (" \ / b f n r t u) into a forward slash. This
    # rescues `"\docs\plan.md"` while leaving `\n` `\t` `\"` intact.
    repaired = re.sub(r'\\(?![\\"/bfnrtu])', "/", text)
    try:
        parsed = json.loads(repaired, strict=False)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    # Final pass : repair qwen's malformed commas (`,,`, leading /
    # trailing commas) on top of the backslash fix, then retry. This
    # is the Phase 2.C.3 blocker — without it `update_document` calls
    # are silently dropped and the model's raw `<tool_call>` text is
    # echoed to the user as if it were the answer.
    comma_fixed = _strip_json_comma_artifacts(repaired)
    try:
        parsed = json.loads(comma_fixed, strict=False)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _extract_inline_tool_calls(content: str) -> list[dict[str, Any]]:
    """Fallback for models that emit the tool call as TEXT in the
    message content rather than the structured `tool_calls` field.
    Scans every `{` that follows a `<tool_call>` marker (or, when
    there is no marker, the first `{` in the message) with a
    brace-balanced reader, then tolerant-parses the slice. Recognises
    the `{"name", "arguments"}` shape qwen2.5:3b emits."""
    if not content or ("{" not in content):
        return []
    out: list[dict[str, Any]] = []
    # Candidate start offsets : every `{` that appears AT or AFTER a
    # `<tool_call>` tag. If there are no tags, fall back to every `{`
    # (cheap — most messages have one or zero).
    starts: list[int] = []
    if "<tool_call>" in content:
        for m in re.finditer(r"<tool_call>", content):
            brace = content.find("{", m.end())
            if brace != -1:
                starts.append(brace)
    else:
        starts = [i for i, ch in enumerate(content) if ch == "{"]
    seen: set[str] = set()
    for start in starts:
        slice_ = _scan_balanced_object(content, start)
        if slice_ is None or slice_ in seen:
            continue
        seen.add(slice_)
        obj = _lenient_json_object(slice_)
        if not isinstance(obj, dict):
            continue
        name = obj.get("name")
        args = obj.get("arguments")
        if not isinstance(name, str) or not isinstance(args, dict):
            continue
        out.append(
            {"id": f"inline_{len(out)}", "name": name, "arguments": args},
        )
    return out


def parse_tool_calls(message: Any) -> list[dict[str, Any]]:
    """Extract `tool_calls` from a non-streaming C8 ChatMessage.

    Two sources, in order :
      1. The OpenAI structured `tool_calls` field (lands in
         `model_extra` because ChatMessage uses extra='allow'). Big
         models (Claude / GPT) take this path.
      2. **Fallback** : the `<tool_call>{...}</tool_call>` text block
         small local models (qwen2.5:3b) emit in the message content.
         This is the C3 analogue of the C4 dispatcher's tolerant
         envelope parser — without it, qwen-class models are unusable
         for tool-driven DocGen (observed 2026-05-18).

    Returns `{id, name, arguments(dict)}` items ; empty when the model
    produced a plain answer. Tolerant : malformed argument JSON
    degrades to `{}` so the executor surfaces a clean error rather
    than crashing the loop."""
    out: list[dict[str, Any]] = []
    extra = getattr(message, "model_extra", None) or {}
    raw = extra.get("tool_calls")
    if isinstance(raw, list):
        for tc in raw:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") or {}
            name = fn.get("name")
            if not isinstance(name, str):
                continue
            args_raw = fn.get("arguments")
            if isinstance(args_raw, dict):
                args = args_raw
            elif isinstance(args_raw, str):
                args = _lenient_json_object(args_raw) or {}
            else:
                args = {}
            out.append(
                {
                    "id": str(tc.get("id") or f"call_{len(out)}"),
                    "name": name,
                    "arguments": args,
                },
            )
    if out:
        return out
    # Structured field empty → try the inline-text fallback.
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return _extract_inline_tool_calls(content)
    return []
