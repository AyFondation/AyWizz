# Session — C9 MCP Server

**Date:** 2026-04-23

## Outcomes

- `c9_mcp/` module implemented as a **stateless JSON-RPC 2.0 wrapper** over C5 (Requirements) + C6 (Validation) facades.
- 8 MCP tools exposed:
  - **C5 read-only** (5): `c5_list_entities`, `c5_get_entity`, `c5_list_documents`, `c5_get_document`, `c5_list_relations`.
  - **C6 read+trigger** (3): `c6_list_plugins`, `c6_trigger_validation`, `c6_list_findings`.
- 3 REST endpoints: `POST /api/v1/mcp` (JSON-RPC), `GET /api/v1/mcp/tools` (admin/debug), `GET /api/v1/mcp/health`.
- 3 contracts registered: `JSONRPCRequest`, `JSONRPCResponse`, `ToolSpec` (consumer: `external_mcp_client`).
- 52 tests added (31 unit + 7 contract + 14 integration with real C5 + C6 round-trip via ArangoDB + MinIO testcontainers).

## Decisions

- **Transport**: HTTP + JSON-RPC 2.0. stdio rejected (C9 deployed in K8s behind C1).
- **v1 scope = read-only + validation trigger**. Requirement mutations via MCP deferred to v2 (distinct security surface).
- **MCP SDK**: not available in sandbox. Implemented protocol from scratch (minimal: `initialize`, `tools/list`, `tools/call`). Keeps the module lean and dependency-free.
- **Error model split**:
  - `ToolDispatchError` (bad arguments) → `isError=true` envelope within a valid JSON-RPC `result`. Visible to the LLM client.
  - `HTTPException` from C5/C6 → same: `isError=true` with the HTTP status text. MCP clients can render the message.
  - Unexpected `Exception` → transport-level JSON-RPC `error` (code `-32002`). Signals the failure is NOT domain-side.
- **Auth**: forward-auth headers (`X-User-Id`) required. No MCP-native auth.
- **Build-time wiring**: `build_default_toolset(c5_service, c6_service)` returns the full tool roster. The roster is a contract test fixture so drift breaks CI.

## Nothing clever, deliberately

C9 is intentionally a **thin layer**. No caching, no retry, no in-memory state, no protocol extensions beyond the three methods actually used. R-100-015 states: *"SHALL NOT implement business logic of its own. Disabling or removing C9 SHALL NOT affect the functionality of any other component."* — respected.

## Coverage

- 596 tests passing, coverage **90.70%** global.
- C9 files 93.94–96.63% per file (no file under the 80% gate).

## Next

Étape 1 backbone complete (C1-C9). User to pick the next push (see SESSION-STATE §5).
