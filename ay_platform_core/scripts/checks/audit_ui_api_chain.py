#!/usr/bin/env python3
# =============================================================================
# File: audit_ui_api_chain.py
# Version: 2
# Path: ay_platform_core/scripts/checks/audit_ui_api_chain.py
# Description: Joins every HTTP call the UI makes to the SHAPES the backend
#              actually accepts and serves, in BOTH directions, and reports
#              the field-level drift.
#
#              v2 adds the REQUEST direction (`compare_request`): the body
#              the client sends and the query parameters it names, against
#              the route's `requestBody` schema and its `required` query
#              params. Severity there is SYMMETRIC — the request models are
#              `extra="forbid"`, so an unexpected key is a 422 and a missing
#              `required` field is a 422 too. It found nothing, which is
#              itself informative: a bad request key is an immediate loud
#              422 in development, a bad response field a silent
#              `undefined`, and that asymmetry is why every defect this
#              audit has found sat on the response side.
#
#              v2 also splits `required` from NULLABLE, which were
#              conflated. In JSON Schema `required` means the KEY is always
#              present and says nothing about the value, so Pydantic's
#              `trigram: str | None` is required AND nullable — the UI's
#              `string | null` was right and the single combined rule
#              reported seventeen false positives on it. Splitting them
#              also exposed the check that was missing entirely:
#              `ui_ignores_nullable`, a value the server may send as `null`
#              that the UI declares non-nullable.
#
#              WHY THIS EXISTS. Five of the six links in the UI→API chain are
#              already guarded, each by its own check:
#
#                UI client → route exists   `ay_platform_ui/tests/contract/
#                                            api-surface.test.ts`
#                catalogue → live routers   `tests/coherence/
#                                            test_route_catalog.py`
#                catalogue → UI snapshot    `tests/coherence/
#                                            test_ui_contract_snapshot.py`
#                catalogue → Traefik+gate   `tests/coherence/
#                                            test_gateway_route_coverage.py`
#                endpoint  → a test exists  `scripts/checks/
#                                            audit_functional_coverage.py`
#
#              The gateway link is covered for UI calls TRANSITIVELY: every
#              path the client emits is pinned to the catalogue by
#              `api-surface.test.ts`, and every catalogued path is pinned to
#              its winning Traefik router by `test_gateway_route_coverage.py`.
#              This script deliberately does NOT re-assert any of that.
#
#              The SIXTH link had nothing. Nothing in this repository reads an
#              OpenAPI schema (`grep -rn openapi tests/ scripts/` is empty),
#              so a renamed or retyped response field is invisible to the
#              whole suite: the backend's own tests assert the new name, and
#              the UI's tests assert the old one against an MSW mock that
#              encodes the UI's belief rather than the server's behaviour.
#              That is the same shape as the upload-405 defect — two sides
#              each internally consistent and disagreeing with each other —
#              and it fails at RUNTIME as an `undefined` rendered into the
#              page, with every tier green.
#
#              THE JOIN KEY was already in the source and unused: the type
#              argument at the call site.
#
#                  this.request<ProjectList>("/api/v1/projects", …)
#                               ^^^^^^^^^^^   ^^^^^^^^^^^^^^^^^
#                               TS type       (method, path) → OpenAPI
#
#              So `(method, path)` resolves the backend's declared response
#              schema and the type argument resolves the UI's declared view
#              of it. Comparing the two is mechanical, which is the point:
#              it pre-chews the surface so that only the flagged rows need a
#              human (or an LLM) to look at them.
#
#              WHERE THE BACKEND TRUTH COMES FROM. Each component's
#              PRODUCTION composition root (`ay_platform_core/<c>/main.py`,
#              `app = create_app()` at module level, `lifespan` not run on
#              import) is imported and asked for `app.openapi()`. No
#              hand-maintained router/prefix table, therefore none to drift:
#              the prefixes are the deployed ones.
#
#              WHAT IT CANNOT SEE. Response bodies not declared through a
#              `response_model` (FastAPI then emits a bare object schema),
#              `Any`-typed fields, and streaming/SSE surfaces. Those are
#              reported as `UNDECLARED` rather than silently passed, so the
#              audit's blind spots are countable instead of invisible.
#
# Usage:
#   # human-readable chain report, every row
#   python ay_platform_core/scripts/checks/audit_ui_api_chain.py
#
#   # findings only
#   python ay_platform_core/scripts/checks/audit_ui_api_chain.py --findings
#
#   # CI / coherence gate: non-zero exit when a BLOCKING finding exists
#   python ay_platform_core/scripts/checks/audit_ui_api_chain.py --check
#
#   # machine-readable, for a later tool or a diff between two revisions
#   python ay_platform_core/scripts/checks/audit_ui_api_chain.py --json
# =============================================================================

from __future__ import annotations

import argparse
import importlib
import json
import re
import sys
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve()
_SUBPROJECT_ROOT = _HERE.parents[2]  # .../ay_platform_core/
_REPO = _HERE.parents[3]
sys.path.insert(0, str(_SUBPROJECT_ROOT))

_API_CLIENT = _REPO / "ay_platform_ui" / "lib" / "apiClient.ts"

_UI_LIB = _REPO / "ay_platform_ui" / "lib"

#: Modules declaring UI wire types, keyed by the specifier `apiClient.ts`
#: imports them under. BOTH are needed: reading only `types.ts` left the
#: sixteen C5 workbench surfaces (`BaselineManifestView`,
#: `ChangeTicketView`, `DocObjectList`, …) unaudited for shape — reported
#: as `LOCAL`, i.e. "no mirror to compare against", which was wrong: the
#: mirror lived in the other file.
#:
#: Keyed by specifier rather than merged into one namespace because the
#: two modules COLLIDE: each declares a different `Finding`. Merging them
#: let one shadow the other and produced eight confident, wrong findings.
#: `apiClient.ts` says which one it means, per import statement, so that
#: is what gets read.
_UI_TYPE_MODULES: dict[str, Path] = {
    "./types": _UI_LIB / "types.ts",
    "./workbenchTypes": _UI_LIB / "workbenchTypes.ts",
}

#: The components serving HTTP behind C1. Mirrors `_EXPECTED_SERVICE` in
#: `tests/coherence/test_gateway_route_coverage.py`; each name is also the
#: `COMPONENT_MODULE` value its K8s Deployment sets, so `<name>.main` is the
#: module the container actually runs.
_HTTP_COMPONENTS: tuple[str, ...] = (
    "c2_auth",
    "c3_conversation",
    "c4_orchestrator",
    "c5_requirements",
    "c6_validation",
    "c7_memory",
    "c8_admin",
    "c9_mcp",
    "c16_backup",
)

#: TS type arguments that declare "no body" or "not a modelled object".
#: `void` is the 204 case; `unknown`/`Blob` are deliberate opt-outs.
_OPAQUE_TS_TYPES = frozenset({"void", "unknown", "Blob", "string", "number", "boolean"})


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


#: Blocking finding codes. A REQUIRED field the UI reads that the server
#: does not send is a RUNTIME `undefined` in the page — the defect class
#: this audit exists for, and the three it found on its first run were all
#: of it. A type disagreement is equally blocking: `number` read as
#: `string` breaks `.toFixed()` at the call site, not at the boundary.
#:
#: An OPTIONAL TS field (`x?:` or `| null`) absent from the schema is NOT
#: blocking — the declaration already says "may be absent", so the UI
#: handles it. It is still reported (`ui_reads_optional_absent_field`),
#: because a field the server can never send is dead code in the UI and
#: usually marks a half-finished migration.
#: `enum_member_unhandled` joins them: a state the server can send and the
#: UI's union cannot name makes every exhaustive branch on that field fall
#: through. C6 serves `RunStatus.PENDING` while the UI declared `"queued"`,
#: so a pending run rendered "the run completed without issues".
#: The REQUEST-direction codes are blocking SYMMETRICALLY, unlike the
#: response ones: the request models are `extra="forbid"`, so a key the
#: server does not accept is a 422, and a `required` field the client
#: never sends is a 422 as well.
#: `ui_ignores_nullable` blocks for the same reason as
#: `ui_reads_absent_field`: a value the server may send as `null` that the
#: UI declares non-nullable is a dereference the compiler will not guard.
BLOCKING = frozenset(
    {
        "ui_reads_absent_field",
        "ui_ignores_nullable",
        "field_type_mismatch",
        "enum_member_unhandled",
        "ui_sends_unaccepted_field",
        "ui_omits_required_field",
        "ui_omits_required_query_param",
    }
)


@dataclass(frozen=True)
class Finding:
    code: str
    client_method: str
    http: str
    path: str
    detail: str

    @property
    def blocking(self) -> bool:
        return self.code in BLOCKING


# ---------------------------------------------------------------------------
# Stage 1 — what the UI calls, and with which declared response type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UiBody:
    """What the client sends, as the source declares it.

    `kind` says how much can be compared:
      · `type`  — `JSON.stringify(payload)` where the enclosing method
                  types `payload`, e.g. `body: LLMModelUpsert`. Compared
                  field by field, like a response.
      · `keys`  — `JSON.stringify({ username, password })`. Only the key
                  NAMES are knowable, which still catches the 422 class.
      · `form`  — `FormData`. Multipart; nothing to compare here.
      · `none`  — no body in the init (GET/DELETE).
      · `?`     — a body the extractor could not classify. Reported, so
                  it is countable rather than quietly uncompared.
    """

    kind: str
    ts_type: str | None = None
    keys: frozenset[str] = frozenset()


@dataclass(frozen=True)
class UiCall:
    client_method: str
    http: str
    path: str
    ts_type: str
    line: int
    body: UiBody = UiBody(kind="none")
    #: Query-parameter names the enclosing method mentions literally.
    query_params: frozenset[str] = frozenset()


def _read_balanced_generic(src: str, start: int) -> tuple[str, int] | None:
    """Read a `<...>` type argument from `start` (index of `<`).

    Angle brackets nest (`Record<string, string[]>`), so a non-greedy regex
    is wrong. Returns the inner text and the index just past the closing
    `>`.
    """
    if start >= len(src) or src[start] != "<":
        return None
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "<":
            depth += 1
        elif src[i] == ">":
            depth -= 1
            if depth == 0:
                return src[start + 1 : i], i + 1
    return None


def _read_path_literal(src: str, start: int) -> tuple[str, int] | None:
    """Read the first string or template literal at/after `start`.

    Skips whitespace and newlines so the multi-line call style
    (`this.request<T>(\\n  `/api/...`,\\n  {…})`) parses the same as the
    single-line one — 76 of the client's call sites use it.

    Template substitutions collapse to `{}`: `${encodeURIComponent(pid)}`
    is a path PARAMETER, and the backend's own template renders to `{}`
    too, so the two become comparable without either side knowing the
    other's naming.
    """
    i = start
    while i < len(src) and src[i] in " \t\r\n":
        i += 1
    if i >= len(src) or src[i] not in "\"'`":
        return None
    quote = src[i]
    i += 1
    out: list[str] = []
    while i < len(src):
        ch = src[i]
        if ch == "\\":
            i += 2
            continue
        if ch == quote:
            break
        if quote == "`" and src.startswith("${", i):
            depth = 0
            j = i + 1
            while j < len(src):
                if src[j] == "{":
                    depth += 1
                elif src[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            out.append("{}")
            i = j + 1
            continue
        out.append(ch)
        i += 1
    # `i` is the closing quote; the caller needs it to find the `init`
    # object that follows. Without that boundary a GET's search window
    # ran into the NEXT method and borrowed its `body:` — seventeen
    # body-less calls were reported as having an unclassifiable body.
    return "".join(out), i + 1


_METHOD_RE = re.compile(r"^  (?:private |protected )?async (\w+)\(", re.M)


def _method_spans(src: str) -> list[tuple[str, int, int]]:
    """`(name, start, end)` for every method of the client class."""
    starts = [(m.group(1), m.start()) for m in _METHOD_RE.finditer(src)]
    spans: list[tuple[str, int, int]] = []
    for i, (name, start) in enumerate(starts):
        end = starts[i + 1][1] if i + 1 < len(starts) else len(src)
        spans.append((name, start, end))
    return spans


def _enclosing_span(
    spans: list[tuple[str, int, int]], index: int
) -> tuple[str, int, int]:
    """The method span containing `index`.

    Needed for more than reporting: the REQUEST direction reads the
    method's parameter types (to resolve `JSON.stringify(payload)`) and
    scans its body for literal query-parameter names.
    """
    for name, start, end in spans:
        if start <= index < end:
            return name, start, end
    # A `this.request` outside any method — only the private `request`
    # declaration itself, which `parse_ui_calls` skips before asking.
    return "?", index, index


_PARAM_TYPE_RE = re.compile(r"\b(\w+)\s*:\s*([A-Za-z_][\w.]*)")
_QUERY_LITERAL_RE = re.compile(r"[?&]([a-z_][a-z0-9_]*)=")
_QUERY_SETTER_RE = re.compile(r"params\.(?:set|append)\(\"([^\"]+)\"")


def _declared_param_type(signature: str, identifier: str) -> str | None:
    """Declared TS type of `identifier` in a method signature.

    `async createLlmRegistryModel(body: LLMModelUpsert)` → `LLMModelUpsert`,
    which is a named interface and therefore comparable against the
    backend's `requestBody` schema in full. This is why the
    `JSON.stringify(<identifier>)` form audits BETTER than an inline
    literal, not worse.
    """
    for match in _PARAM_TYPE_RE.finditer(signature):
        if match.group(1) == identifier:
            return match.group(2)
    return None


def _inline_param_type(signature: str, identifier: str) -> str | None:
    """The `{ … }` type literal declared for `identifier`, if it has one."""
    match = re.search(rf"\b{re.escape(identifier)}\s*:\s*(?=\{{)", signature)
    if match is None:
        return None
    inner = _read_balanced(signature, match.end(), "{", "}")
    # Re-braced: `_object_literal_keys` is the single key extractor for
    # value and type literals alike, and it keys off the leading `{`.
    return None if inner is None else "{" + inner + "}"


def _object_literal_keys(text: str) -> frozenset[str]:
    """Top-level keys of a `{ … }` literal, value or type.

    Handles all three spellings the client uses: `key: value` and
    shorthand `key` in a VALUE literal (`JSON.stringify({ username,
    password })`), and `key?: Type;` in an inline TYPE literal
    (`body: { dry_run: boolean; new_project_id?: string | null }`). `,`
    and `;` are both treated as separators — neither can occur inside a
    key name — and `?` terminates the key naturally because `\\w` excludes
    it.

    Depth-aware, for the same reason `_split_members` is: a nested
    literal's keys are not the outer object's keys, and treating them as
    such invents fields. A `...spread` entry is SKIPPED rather than
    guessed at, so this can under-report; it cannot invent.
    """
    inner = text.strip()
    if not inner.startswith("{"):
        return frozenset()
    inner = inner[1:-1] if inner.endswith("}") else inner[1:]
    keys: set[str] = set()
    depth = 0
    current: list[str] = []

    def _take(chunk: str) -> None:
        if "..." in chunk:
            return
        if m := re.match(r"^\s*(\w+)", chunk):
            keys.add(m.group(1))

    for ch in inner:
        if ch in "{([<":
            depth += 1
        elif ch in "})]>":
            depth -= 1
        if ch in ",;" and depth == 0:
            _take("".join(current))
            current = []
            continue
        current.append(ch)
    _take("".join(current))
    return frozenset(keys)


def _read_balanced(src: str, start: int, opener: str, closer: str) -> str | None:
    """Text inside the balanced `opener…closer` pair starting at `start`."""
    if start >= len(src) or src[start] != opener:
        return None
    depth = 0
    for i in range(start, len(src)):
        if src[i] == opener:
            depth += 1
        elif src[i] == closer:
            depth -= 1
            if depth == 0:
                return src[start + 1 : i]
    return None


def _stringified_body(inner: str, signature: str) -> UiBody:
    """Classify the argument of a `JSON.stringify(…)` body."""
    inner = inner.strip()
    if inner.startswith("{"):
        return UiBody(kind="keys", keys=_object_literal_keys(inner))
    identifier = re.match(r"^(\w+)", inner)
    if identifier is None:
        return UiBody(kind="?")
    declared = _declared_param_type(signature, identifier.group(1))
    if declared is not None:
        return UiBody(kind="type", ts_type=declared)
    # The parameter's type can itself be an INLINE literal —
    # `restoreBackup(…, body: { dry_run: boolean; new_project_id?: string
    # | null })`. There is no named interface to compare, but the key
    # names are right there, and names alone catch the 422 class.
    inline = _inline_param_type(signature, identifier.group(1))
    if inline is None:
        return UiBody(kind="?")
    return UiBody(kind="keys", keys=_object_literal_keys(inline))


def _extract_body(window: str, signature: str) -> UiBody:
    """Classify the `body:` of one `request()` init object."""
    match = re.search(r"\bbody:\s*", window)
    if match is None:
        return UiBody(kind="none")
    rest = window[match.end() :]
    if rest.startswith("JSON.stringify("):
        inner = _read_balanced(rest, len("JSON.stringify"), "(", ")")
        return UiBody(kind="?") if inner is None else _stringified_body(inner, signature)
    # `body: form` where `form` is a FormData — multipart, out of scope.
    # `$` matters: `_read_balanced` hands back the init's INNER text, so
    # the last entry has no trailing `,` or `}` to match against.
    if re.match(r"^\w+\s*([,}]|$)", rest.strip()):
        return UiBody(kind="form")
    return UiBody(kind="?")


def parse_ui_calls(source: str) -> tuple[list[UiCall], list[str]]:
    """Extract every `this.request<T>(path, { method: … })` call site.

    Returns the calls plus a list of call sites that could NOT be parsed.
    The second half matters more than the first: a silently-skipped call
    site would shrink the audit without shrinking its claim, which is the
    failure mode that makes a check vacuous. The caller turns unparsed
    sites into findings.
    """
    calls: list[UiCall] = []
    unparsed: list[str] = []
    spans = _method_spans(source)
    needle = "this.request"
    pos = 0
    while True:
        at = source.find(needle, pos)
        if at < 0:
            break
        pos = at + len(needle)
        line = source.count("\n", 0, at) + 1
        generic = _read_balanced_generic(source, pos)
        if generic is None:
            # `private async request<T>(…)` — the declaration itself.
            continue
        ts_type, after = generic
        if after >= len(source) or source[after] != "(":
            unparsed.append(f"line {line}: generic not followed by `(`")
            continue
        parsed_path = _read_path_literal(source, after + 1)
        if parsed_path is None:
            unparsed.append(f"line {line}: first argument is not a literal path")
            continue
        path, path_end = parsed_path
        # The `init` object literal is the SECOND argument, and it has to
        # be delimited exactly rather than approximated by a fixed-size
        # window: a body-less GET's window ran past the end of its own
        # method and matched the NEXT method's `body:`, which reported
        # seventeen body-less calls as having an unclassifiable body.
        brace = source.find("{", path_end)
        init = _read_balanced(source, brace, "{", "}") if brace >= 0 else None
        window = init if init is not None else source[after : after + 600]
        mm = re.search(r'method:\s*"([A-Z]+)"', window)
        if mm is None:
            unparsed.append(f"line {line}: no `method: \"…\"` within the call")
            continue
        # REQUEST direction. The method's own text supplies both halves:
        # its signature types the `JSON.stringify(<identifier>)` body, and
        # its body carries the query-parameter names as literal strings
        # (`?limit=`, `params.set("x"`) even when they are assembled into
        # a variable before interpolation.
        name, m_start, m_end = _enclosing_span(spans, at)
        method_src = source[m_start:m_end]
        # The parameter list, delimited by BALANCED PARENS. Stopping at
        # the first `{` instead (as this did) truncated the signature at
        # a parameter's own inline type literal — `restoreBackup(…, body:
        # { dry_run: boolean; … })` — so its keys were unreachable.
        paren = method_src.find("(")
        params = _read_balanced(method_src, paren, "(", ")") if paren >= 0 else None
        signature = params or ""
        body = _extract_body(window, signature)
        if body.kind == "?":
            unparsed.append(
                f"line {line}: `body:` present but not classifiable "
                "— the REQUEST shape of this call is unaudited"
            )
        calls.append(
            UiCall(
                client_method=name,
                http=mm.group(1),
                path=path.split("?", 1)[0],
                ts_type=ts_type.strip(),
                line=line,
                body=body,
                query_params=frozenset(_QUERY_LITERAL_RE.findall(method_src))
                | frozenset(_QUERY_SETTER_RE.findall(method_src)),
            )
        )
    return calls, unparsed


# ---------------------------------------------------------------------------
# Stage 2 — what the backend declares it serves
# ---------------------------------------------------------------------------


@dataclass
class BackendRoute:
    component: str
    http: str
    path: str
    #: Resolved 2xx JSON response schema, or None when the route declares
    #: no `response_model` (reported as UNDECLARED, never as "fine").
    schema: dict[str, Any] | None
    schemas: dict[str, Any] = field(repr=False, default_factory=dict)
    #: `requestBody` JSON schema, None when the route takes no body.
    request_schema: dict[str, Any] | None = None
    #: Query parameters the route declares as REQUIRED. One of these not
    #: sent by the client is a guaranteed 422.
    required_query: frozenset[str] = frozenset()


def _deref(node: dict[str, Any], schemas: dict[str, Any], seen: frozenset[str]) -> dict[str, Any]:
    """Follow a single `$ref` into `components.schemas`.

    `seen` breaks the recursive-model cycle (`ArtifactNode.children:
    ArtifactNode[]`), which would otherwise recurse forever.
    """
    ref = node.get("$ref")
    if not isinstance(ref, str):
        return node
    name = ref.rsplit("/", 1)[-1]
    if name in seen:
        return {}
    target = schemas.get(name)
    return target if isinstance(target, dict) else {}


def _request_schema(operation: dict[str, Any]) -> dict[str, Any] | None:
    """JSON `requestBody` schema of an operation, if it takes one."""
    content = (operation.get("requestBody") or {}).get("content") or {}
    body = content.get("application/json")
    if body is None:
        return None
    schema = body.get("schema")
    return schema if isinstance(schema, dict) and schema else None


def _required_query_params(operation: dict[str, Any]) -> frozenset[str]:
    """Names of the operation's REQUIRED query parameters."""
    return frozenset(
        str(p.get("name"))
        for p in operation.get("parameters") or []
        if isinstance(p, dict) and p.get("in") == "query" and p.get("required")
    )


def _success_schema(operation: dict[str, Any], schemas: dict[str, Any]) -> dict[str, Any] | None:
    for status, resp in (operation.get("responses") or {}).items():
        if not str(status).startswith("2"):
            continue
        content = (resp or {}).get("content") or {}
        body = content.get("application/json")
        if body is None:
            continue
        schema = body.get("schema")
        if not isinstance(schema, dict) or not schema:
            continue
        return schema
    return None


def load_backend_routes() -> tuple[
    dict[tuple[str, str], BackendRoute], list[str], list[str]
]:
    """Harvest OpenAPI from every component's PRODUCTION app.

    Keyed by `(HTTP method, normalised path)` where normalised means every
    `{param}` — catch-all converters included — collapsed to `{}`, matching
    what `_read_path_literal` does to the client's template substitutions.

    `app.openapi()` is called under a LOCAL warning filter. Not to hide
    anything: `pyproject.toml` sets `filterwarnings = error`, so when this
    audit runs under pytest FastAPI's "Duplicate Operation ID" warning was
    raised as an exception and C2's entire route set silently dropped out —
    twenty-two UI calls went unaudited while every assertion still passed.
    The warnings are therefore caught rather than suppressed, and returned
    as advisory findings: a duplicate `operationId` makes the emitted
    document invalid OpenAPI, which matters the moment anyone generates a
    client from it.

    Returns `(routes, import_errors, schema_warnings)`.
    """
    routes: dict[tuple[str, str], BackendRoute] = {}
    errors: list[str] = []
    notices: list[str] = []
    for component in _HTTP_COMPONENTS:
        module_name = f"ay_platform_core.{component}.main"
        try:
            module = importlib.import_module(module_name)
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                spec = module.app.openapi()
            for entry in caught:
                notices.append(f"{component}: {entry.message}")
        except Exception as exc:  # reported as a finding, not swallowed
            errors.append(f"{module_name}: {type(exc).__name__}: {exc}")
            continue
        schemas = (spec.get("components") or {}).get("schemas") or {}
        for raw_path, operations in (spec.get("paths") or {}).items():
            norm = re.sub(r"\{[^}]+\}", "{}", raw_path)
            for http, operation in operations.items():
                if http.upper() not in {
                    "GET",
                    "POST",
                    "PUT",
                    "PATCH",
                    "DELETE",
                }:
                    continue
                routes[(http.upper(), norm)] = BackendRoute(
                    component=component,
                    http=http.upper(),
                    path=raw_path,
                    schema=_success_schema(operation, schemas),
                    schemas=schemas,
                    request_schema=_request_schema(operation),
                    required_query=_required_query_params(operation),
                )
    return routes, errors, notices


# ---------------------------------------------------------------------------
# Stage 3 — what the UI believes it receives
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TsField:
    name: str
    optional: bool
    #: The declared type with `| null`/`| undefined` stripped, e.g.
    #: `Project[]`, `string`, `ProjectStatus`.
    type: str
    nullable: bool


@dataclass
class TsInterface:
    name: str
    fields: dict[str, TsField]
    extends: tuple[str, ...]


_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT_RE = re.compile(r"^\s*//.*$", re.M)


def _split_members(body: str) -> list[str]:
    """Split an interface body into members, on `;` at BRACE DEPTH ZERO.

    A naive `body.split(";")` was the audit's worst bug: it shredded a
    field whose type is an INLINE OBJECT LITERAL. From

        impact: { seed_id: string; nodes: ImpactNodeView[] };

    it produced a field `impact` of type `{ seed_id: string` plus a
    PHANTOM top-level field `nodes` — and since the backend's model has
    no `nodes` at the top level, the audit reported a confident blocking
    defect that did not exist. Eight of the ten findings on the first run
    over `workbenchTypes.ts` were this, in `ChangeTicketView`,
    `TreatmentPlanView` and `PlanStepView`.

    A parser that invents fields is worse than one that skips them: it
    spends the reader's trust. Depth tracking also covers `<…>` generics,
    which can carry a `;`-free but comma-heavy payload.
    """
    out: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in body:
        if ch in "{([<":
            depth += 1
        elif ch in "})]>":
            depth -= 1
        if ch == ";" and depth == 0:
            out.append("".join(current))
            current = []
            continue
        current.append(ch)
    out.append("".join(current))
    return out


def parse_ts_interfaces(source: str) -> dict[str, TsInterface]:
    """Parse `export interface X { … }` declarations from `lib/types.ts`.

    Deliberately narrow: this file is a hand-written declarative wire-format
    mirror ("snake_case fields match the Python wire format verbatim so
    there's no mapping layer to keep in sync", per its own header), so a
    field is `name?: type;` and nothing more exotic. A construct the parser
    does not understand yields a field it cannot see, which the comparison
    below would read as agreement — so the parser records the body it
    skipped instead, and `_unparsed_fields` turns it into a finding.
    """
    clean = _LINE_COMMENT_RE.sub("", _COMMENT_RE.sub("", source))
    out: dict[str, TsInterface] = {}
    for m in re.finditer(
        r"export interface (\w+)(?:\s+extends\s+([\w\s,]+?))?\s*\{", clean
    ):
        name = m.group(1)
        extends = tuple(
            part.strip() for part in (m.group(2) or "").split(",") if part.strip()
        )
        depth = 0
        end = m.end() - 1
        for i in range(m.end() - 1, len(clean)):
            if clean[i] == "{":
                depth += 1
            elif clean[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        body = clean[m.end() : end]
        fields: dict[str, TsField] = {}
        for raw in _split_members(body):
            stmt = raw.strip()
            if not stmt:
                continue
            fm = re.match(r"^(\w+)(\?)?\s*:\s*(.+)$", stmt, re.S)
            if fm is None:
                continue
            decl = " ".join(fm.group(3).split())
            parts = [p.strip() for p in decl.split("|")]
            nullable = any(p in ("null", "undefined") for p in parts)
            concrete = [p for p in parts if p not in ("null", "undefined")]
            fields[fm.group(1)] = TsField(
                name=fm.group(1),
                optional=fm.group(2) == "?",
                type=concrete[0] if len(concrete) == 1 else decl,
                nullable=nullable,
            )
        out[name] = TsInterface(name=name, fields=fields, extends=extends)
    return out


_IMPORT_RE = re.compile(r"import type \{([^}]*)\} from \"([^\"]+)\";", re.S)


def parse_apiclient_type_imports(source: str) -> dict[str, str]:
    """Map each imported type name to the module specifier it came from.

    This is the collision resolver. `lib/types.ts` and
    `lib/workbenchTypes.ts` both export a `Finding`, and merging the two
    modules into one namespace made the second shadow the first — eight
    findings that read as real defects and were artefacts of the merge.
    TypeScript resolves the name per import statement; so does this.
    """
    out: dict[str, str] = {}
    for match in _IMPORT_RE.finditer(source):
        specifier = match.group(2)
        for raw in match.group(1).split(","):
            name = raw.strip()
            if name:
                out[name] = specifier
    return out


_TS_UNION_RE = re.compile(r"export type (\w+)\s*=\s*([^;]+);")


def parse_ts_string_unions(source: str) -> dict[str, frozenset[str]]:
    """Parse `export type X = "a" | "b" | "c";` aliases from `lib/types.ts`.

    These are the UI's enums, and they drift independently of the field
    names: `ValidationRunStatus` declared `"queued"` while C6's `RunStatus`
    serves `"pending"`, so the run page's `status === "queued"` branch was
    dead and a pending run fell through to "the run completed without
    issues". A field-name comparison alone could not see it — the field
    `status` existed on both sides.

    Aliases that are not pure string-literal unions (`keyof typeof …`,
    object types) are skipped: there is no enum to compare.
    """
    clean = _LINE_COMMENT_RE.sub("", _COMMENT_RE.sub("", source))
    out: dict[str, frozenset[str]] = {}
    for m in _TS_UNION_RE.finditer(clean):
        parts = [p.strip() for p in m.group(2).split("|")]
        literals = {p[1:-1] for p in parts if len(p) > 1 and p[0] == p[-1] == '"'}
        if literals and len(literals) == len(parts):
            out[m.group(1)] = frozenset(literals)
    return out


def _schema_enum(node: dict[str, Any], schemas: dict[str, Any]) -> frozenset[str] | None:
    """Enum members a schema property may take, through `$ref`/`anyOf`.

    Returns None when the property is not an enum, so "not an enum" stays
    distinguishable from "an enum with no members".
    """
    resolved = _deref(node, schemas, frozenset())
    values = resolved.get("enum")
    if isinstance(values, list) and values:
        return frozenset(str(v) for v in values)
    for alt in resolved.get("anyOf") or resolved.get("oneOf") or []:
        if not isinstance(alt, dict) or alt.get("type") == "null":
            continue
        nested = _schema_enum(alt, schemas)
        if nested is not None:
            return nested
    return None


def resolve_ts_fields(
    name: str, interfaces: dict[str, TsInterface], seen: frozenset[str] = frozenset()
) -> dict[str, TsField]:
    """Flatten an interface with its `extends` parents."""
    if name in seen or name not in interfaces:
        return {}
    iface = interfaces[name]
    merged: dict[str, TsField] = {}
    for parent in iface.extends:
        merged.update(resolve_ts_fields(parent, interfaces, seen | {name}))
    merged.update(iface.fields)
    return merged


# ---------------------------------------------------------------------------
# Stage 4 — compare one TS type against one JSON schema
# ---------------------------------------------------------------------------

#: TS primitive ↔ JSON Schema `type`. Only disagreements BETWEEN these
#: scalar families are reported; anything outside the table is left alone
#: rather than guessed at, because a false positive here would train the
#: reader to ignore the audit.
_SCALAR_FAMILIES: dict[str, frozenset[str]] = {
    "string": frozenset({"string"}),
    "number": frozenset({"integer", "number"}),
    "boolean": frozenset({"boolean"}),
}


def _schema_is_nullable(node: dict[str, Any], schemas: dict[str, Any]) -> bool:
    """Whether the schema admits `null` as a value.

    SEPARATE FROM `required`, and conflating the two was a real defect in
    this audit. In JSON Schema `required` means the KEY is always present;
    it says nothing about the value. Pydantic's `trigram: str | None`
    (no default) is required AND nullable, so the UI's `string | null` was
    right and only its `?` was wrong — yet the single combined rule
    reported the whole declaration as a mismatch.

    Splitting them also exposes the case the combined rule could not see
    at all: a field the server may send as `null` that the UI does NOT
    declare nullable, which is a dereference waiting to happen.
    """
    resolved = _deref(node, schemas, frozenset())
    if resolved.get("type") == "null":
        return True
    return any(
        isinstance(alt, dict)
        and (alt.get("type") == "null" or _schema_is_nullable(alt, schemas))
        for alt in (resolved.get("anyOf") or resolved.get("oneOf") or [])
    )


def _schema_families(node: dict[str, Any], schemas: dict[str, Any]) -> set[str]:
    """JSON-Schema `type` values a node may take, flattening `anyOf`."""
    out: set[str] = set()
    if "$ref" in node:
        return out
    if isinstance(node.get("type"), str):
        out.add(node["type"])
    if "enum" in node and not out:
        out.add("string")
    for alt in node.get("anyOf") or node.get("oneOf") or []:
        if isinstance(alt, dict):
            out |= _schema_families(alt, schemas)
    out.discard("null")
    return out


def _object_properties(
    node: dict[str, Any], schemas: dict[str, Any], seen: frozenset[str]
) -> tuple[dict[str, Any], set[str], bool] | None:
    """Reduce a schema node to `(properties, required, is_open)`.

    `is_open` means the node admits arbitrary keys (`additionalProperties`
    free, or no `properties` at all) — a dict-shaped response, where a
    field-by-field comparison would invent disagreements.
    """
    node = _deref(node, schemas, seen)
    if not node:
        return None
    for alt in node.get("anyOf") or node.get("oneOf") or []:
        if isinstance(alt, dict) and (alt.get("type") == "object" or "$ref" in alt):
            return _object_properties(alt, schemas, seen)
    if node.get("type") == "array":
        return None
    props = node.get("properties")
    if not isinstance(props, dict) or not props:
        return None
    return props, set(node.get("required") or []), False


def _ts_element_type(declared: str) -> str:
    """`Project[]` → `Project`; `Array<Project>` → `Project`."""
    if declared.endswith("[]"):
        return declared[:-2].strip()
    am = re.match(r"^Array<(.+)>$", declared)
    return am.group(1).strip() if am else declared


def _compare_field(
    call: UiCall,
    route: BackendRoute,
    unions: dict[str, frozenset[str]],
    *,
    here: str,
    field: TsField,
    prop: dict[str, Any],
    is_required: bool,
) -> list[Finding]:
    """The three checks that apply to a field PRESENT on both sides.

    Split out of `compare` so the recursive walk stays readable: that
    function's job is which pairs to visit, this one's is what to say
    about a pair.
    """
    findings: list[Finding] = []
    where = f"{here}.{field.name}"

    if "{" in field.type:
        # An INLINE object literal (`estimate: { tokens: number; … }`).
        # Its members are not compared: there is no named interface to
        # recurse into, and the schema side has a real nested object. Five
        # such fields exist, all in `workbenchTypes.ts`. Reported so the
        # gap is countable — this is the same field shape whose naive
        # `;`-split once invented eight phantom defects, and leaving it
        # silent would hide the residue of that bug.
        findings.append(
            Finding(
                "inline_object_not_compared",
                call.client_method,
                call.http,
                call.path,
                f"{where} is an inline object type; its members are "
                "UNAUDITED. Promote it to a named interface to compare "
                "them.",
            )
        )
        return findings

    ui_members = unions.get(field.type)
    served_members = _schema_enum(prop, route.schemas)
    if ui_members is not None and served_members is not None:
        unservable = served_members - ui_members
        if unservable:
            findings.append(
                Finding(
                    "enum_member_unhandled",
                    call.client_method,
                    call.http,
                    call.path,
                    f"{where}: {route.component} can serve "
                    f"{sorted(unservable)}, absent from the UI's "
                    f"`{field.type}` union",
                )
            )
        dead = ui_members - served_members
        if dead:
            findings.append(
                Finding(
                    "enum_member_dead",
                    call.client_method,
                    call.http,
                    call.path,
                    f"{where}: the UI's `{field.type}` declares "
                    f"{sorted(dead)}, which {route.component} never "
                    "serves — any branch on it is dead",
                )
            )

    families = _schema_families(prop, route.schemas)
    want = _SCALAR_FAMILIES.get(field.type)
    if want is not None and families and not (families & want):
        findings.append(
            Finding(
                "field_type_mismatch",
                call.client_method,
                call.http,
                call.path,
                f"{where}: UI declares `{field.type}`, "
                f"{route.component} serves {sorted(families)}",
            )
        )

    served_nullable = _schema_is_nullable(prop, route.schemas)
    if served_nullable and not field.nullable:
        findings.append(
            Finding(
                "ui_ignores_nullable",
                call.client_method,
                call.http,
                call.path,
                f"{where}: {route.component} may serve `null` and the UI "
                f"declares `{field.type}` — any dereference is unguarded",
            )
        )
    if is_required and field.optional:
        findings.append(
            Finding(
                "ui_optional_backend_required",
                call.client_method,
                call.http,
                call.path,
                f"{where}: the key is ALWAYS present (the `?` is noise)"
                + ("" if not served_nullable else "; its VALUE may be null, "
                   "which `| null` already covers"),
            )
        )
    return findings


def compare_request(
    call: UiCall,
    route: BackendRoute,
    interfaces: dict[str, TsInterface],
) -> list[Finding]:
    """Diff what the client SENDS against what the route accepts.

    WHY THIS IS A SEPARATE FUNCTION from `compare`. The severity is
    REVERSED and symmetric. On a response, a field the server does not
    send breaks the UI while a field the UI ignores is merely unused. On a
    request, BOTH directions are fatal, and for the same reason: every C5
    / C6 / C7 request model is `extra="forbid"`, so an unexpected key is a
    **422**, and a `required` field the client never sends is a **422**
    too. Folding that into `compare` behind a flag would make both halves
    harder to read than keeping them apart.

    This closes the direction the audit could not see until now: paths and
    methods were pinned, response shapes were pinned, and the request body
    was unguarded — the same blind spot, one hop upstream.
    """
    findings: list[Finding] = []

    for name in sorted(route.required_query - call.query_params):
        findings.append(
            Finding(
                "ui_omits_required_query_param",
                call.client_method,
                call.http,
                call.path,
                f"`{name}` is a REQUIRED query parameter of this route and "
                f"{call.client_method} never names it — the request is a "
                "guaranteed 422",
            )
        )

    if route.request_schema is None or call.body.kind in ("none", "form", "?"):
        return findings

    reduced = _object_properties(route.request_schema, route.schemas, frozenset())
    if reduced is None:
        return findings
    props, required, _ = reduced

    if call.body.kind == "keys":
        sent = call.body.keys
    elif call.body.ts_type in interfaces:
        sent = frozenset(resolve_ts_fields(call.body.ts_type, interfaces))
    else:
        # A typed body whose type is not one of the wire modules' — e.g.
        # declared inline in the method. Nothing to compare; reported as
        # an advisory rather than passed over.
        return [
            *findings,
            Finding(
                "request_body_type_unknown",
                call.client_method,
                call.http,
                call.path,
                f"body is typed `{call.body.ts_type}`, which is not declared "
                "in lib/types.ts or lib/workbenchTypes.ts — the REQUEST "
                "shape of this call is unaudited",
            )
        ]

    label = call.body.ts_type or "the body literal"
    for name in sorted(sent - set(props)):
        findings.append(
            Finding(
                "ui_sends_unaccepted_field",
                call.client_method,
                call.http,
                call.path,
                f"{label}.{name} is sent but {route.component} does not "
                "accept it; the request models are `extra=\"forbid\"`, so "
                "this is a 422",
            )
        )
    # Only a REQUIRED omission is reported. An optional one the client
    # chooses not to send is the normal case, not a defect.
    optional_on_ui = (
        {
            name
            for name, f in resolve_ts_fields(call.body.ts_type, interfaces).items()
            if f.optional or f.nullable
        }
        if call.body.kind == "type" and call.body.ts_type in interfaces
        else set()
    )
    for name in sorted((required - sent) | (required & optional_on_ui)):
        findings.append(
            Finding(
                "ui_omits_required_field",
                call.client_method,
                call.http,
                call.path,
                f"{route.component} REQUIRES `{name}` and {label} "
                + ("declares it optional" if name in optional_on_ui else "omits it")
                + " — a 422 whenever it is absent",
            )
        )
    return findings


def _root_list_items(
    ts_name: str,
    body: dict[str, Any],
    route: BackendRoute,
    seen: frozenset[str],
) -> dict[str, Any] | None:
    """Element schema of a response whose ROOT is a list, else None.

    A route declared `response_model=list[X]` with `Promise<X[]>` on the
    client was skipped wholesale before this: the TS name `BackupRecord[]`
    matches no interface, and `_object_properties` declines arrays. Four
    endpoints — every top-level listing the UI consumes — fell out of the
    comparison in silence.
    """
    resolved = _deref(body, route.schemas, seen)
    if resolved.get("type") != "array":
        return None
    items = resolved.get("items")
    return items if isinstance(items, dict) else None


def compare(
    call: UiCall,
    route: BackendRoute,
    interfaces: dict[str, TsInterface],
    unions: dict[str, frozenset[str]],
    *,
    ts_type: str | None = None,
    schema: dict[str, Any] | None = None,
    trail: str = "",
    seen: frozenset[str] = frozenset(),
) -> list[Finding]:
    """Diff one TS interface against one response schema, recursively.

    Recursion follows the pairs the two sides AGREE are composite: a TS
    field whose (element) type names a known interface, against a schema
    property that reduces to an object. That keeps the walk anchored to
    real declarations on both sides instead of exploring one side's shape
    and asserting the other's absence.
    """
    ts_name = ts_type if ts_type is not None else call.ts_type
    body = schema if schema is not None else route.schema
    if body is None or ts_name in _OPAQUE_TS_TYPES:
        return []
    if _ts_element_type(ts_name) != ts_name:
        element_schema = _root_list_items(ts_name, body, route, seen)
        if element_schema is None:
            return []
        return compare(
            call,
            route,
            interfaces,
            unions,
            ts_type=_ts_element_type(ts_name),
            schema=element_schema,
            trail=f"{trail}[]",
            seen=seen,
        )
    if ts_name not in interfaces or ts_name in seen:
        return []
    reduced = _object_properties(body, route.schemas, seen)
    if reduced is None:
        return []
    props, required, _ = reduced
    fields = resolve_ts_fields(ts_name, interfaces)
    findings: list[Finding] = []
    here = f"{ts_name}{trail}"

    for fname, tsf in sorted(fields.items()):
        prop = props.get(fname)
        if prop is None:
            declared_absent = tsf.optional or tsf.nullable
            findings.append(
                Finding(
                    "ui_reads_optional_absent_field"
                    if declared_absent
                    else "ui_reads_absent_field",
                    call.client_method,
                    call.http,
                    call.path,
                    f"{here}.{fname} is read by the UI but "
                    f"{route.component} does not serve it"
                    + (
                        " (optional on the UI side, so it is dead rather "
                        "than broken)"
                        if declared_absent
                        else ""
                    ),
                )
            )
            continue
        findings.extend(
            _compare_field(
                call,
                route,
                unions,
                here=here,
                field=tsf,
                prop=prop,
                is_required=fname in required,
            )
        )
        # Recurse through the composite pairs.
        element = _ts_element_type(tsf.type)
        if element in interfaces:
            nested = prop
            if (_deref(prop, route.schemas, seen)).get("type") == "array":
                nested = (_deref(prop, route.schemas, seen)).get("items") or {}
            findings.extend(
                compare(
                    call,
                    route,
                    interfaces,
                    unions,
                    ts_type=element,
                    schema=nested,
                    trail=f"{trail}.{fname}",
                    seen=seen | {ts_name},
                )
            )

    for pname in sorted(set(props) - set(fields)):
        if pname in required:
            findings.append(
                Finding(
                    "ui_blind_to_served_field",
                    call.client_method,
                    call.http,
                    call.path,
                    f"{here}.{pname} is always served by "
                    f"{route.component} but absent from the UI type",
                )
            )
    return findings


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


@dataclass
class Row:
    call: UiCall
    component: str
    backend_path: str
    schema_state: str
    findings: list[Finding]


@dataclass
class Report:
    rows: list[Row]
    findings: list[Finding]

    @property
    def blocking(self) -> list[Finding]:
        return [f for f in self.findings if f.blocking]

    @property
    def request_states(self) -> dict[str, int]:
        """How each call's REQUEST side was compared, by body kind.

        Separate from the response states because the two directions can
        go vacuous independently: the response comparison was working
        for a whole session while the request side did not exist.
        """
        counts: dict[str, int] = {}
        for row in self.rows:
            counts[row.call.body.kind] = counts.get(row.call.body.kind, 0) + 1
        return counts

    @property
    def query_named(self) -> int:
        """Calls where the extractor found at least one query-param name.

        The metric that says the query-parameter scan still works. It
        reads literal names out of the method body (`?limit=`,
        `params.set("x"`) because the client assembles the string into a
        variable before interpolating it, so a refactor of that idiom
        would silently drop every check.
        """
        return sum(1 for row in self.rows if row.call.query_params)


def _match_route(
    routes: dict[tuple[str, str], BackendRoute], call: UiCall
) -> BackendRoute | None:
    """Resolve a UI call to a backend operation.

    A trailing `{}` is AMBIGUOUS, and getting it wrong silently cost the
    audit twelve of its rows on the first run. The client builds query
    strings into a variable and interpolates it at the end of the template
    (`` `/admin/users${qs}` ``, `` `/api/v1/projects/${pid}/plans${query}` ``),
    which `_read_path_literal` cannot distinguish from a final path
    parameter — both are `${…}`.

    So: try the path as collapsed, then, only if nothing matched, try it
    with the trailing `{}` removed. A genuine trailing path parameter
    matches on the first attempt, so the fallback cannot mask one.
    """
    direct = routes.get((call.http, call.path))
    if direct is not None:
        return direct
    if call.path.endswith("{}"):
        stripped = call.path[:-2].rstrip("/") or "/"
        return routes.get((call.http, stripped))
    return None


#: One module's view of the UI's type world: its interfaces and its
#: string-literal unions.
Namespace = tuple[dict[str, TsInterface], dict[str, frozenset[str]]]


def build_ts_namespaces() -> tuple[dict[str, Namespace], list[str]]:
    """One namespace per wire-type module, plus the names they share.

    A module's namespace is its OWN declarations first, then the other
    module's as a fallback for names it references but does not declare.
    Own-first is what makes the `Finding` collision resolve the way
    TypeScript resolves it — a single merged namespace let
    `workbenchTypes.ts` shadow `types.ts` and produced eight confident,
    wrong findings.
    """
    per_module: dict[str, Namespace] = {}
    for specifier, module in _UI_TYPE_MODULES.items():
        source = module.read_text()
        per_module[specifier] = (
            parse_ts_interfaces(source),
            parse_ts_string_unions(source),
        )

    namespaces: dict[str, Namespace] = {}
    for specifier in _UI_TYPE_MODULES:
        merged_i: dict[str, TsInterface] = {}
        merged_u: dict[str, frozenset[str]] = {}
        for other in _UI_TYPE_MODULES:
            if other != specifier:
                merged_i.update(per_module[other][0])
                merged_u.update(per_module[other][1])
        merged_i.update(per_module[specifier][0])
        merged_u.update(per_module[specifier][1])
        namespaces[specifier] = (merged_i, merged_u)

    declared = [set(interfaces) for interfaces, _ in per_module.values()]
    shared = set.intersection(*declared) if len(declared) > 1 else set()
    return namespaces, sorted(shared)


def _toolchain_findings(
    unparsed: list[str],
    import_errors: list[str],
    schema_notices: list[str],
    collisions: list[str],
) -> list[Finding]:
    """Findings about the audit's own ability to read its inputs.

    These are findings, not warnings printed on the way past: an audit
    that cannot read one side of the comparison reports zero drift, which
    is indistinguishable from agreement. Both of this script's own early
    bugs — the query-string paths and C2 disappearing behind
    `filterwarnings = error` — were of exactly that shape.
    """
    findings: list[Finding] = []
    for note in unparsed:
        findings.append(
            Finding(
                "unparsed_call_site",
                "?",
                "?",
                "?",
                f"{_API_CLIENT.name}: {note} — the audit cannot see this call",
            )
        )
    for note in import_errors:
        findings.append(
            Finding(
                "component_unreadable",
                "?",
                "?",
                "?",
                f"{note} — every route of this component is unaudited",
            )
        )
    for note in schema_notices:
        findings.append(Finding("openapi_schema_warning", "?", "?", "?", note))
    for name in collisions:
        findings.append(
            Finding(
                "colliding_ui_type_name",
                "?",
                "?",
                "?",
                f"`{name}` is declared by BOTH lib/types.ts and "
                "lib/workbenchTypes.ts. Resolved per import statement, as "
                "TypeScript does — but two unrelated types sharing a name "
                "in one UI is a readability trap, and merging the two "
                "namespaces (as this audit first did) produced eight "
                "confident, wrong findings.",
            )
        )
    return findings


def audit() -> Report:
    client_src = _API_CLIENT.read_text()
    calls, unparsed = parse_ui_calls(client_src)
    type_origin = parse_apiclient_type_imports(client_src)

    namespaces, collisions = build_ts_namespaces()
    default_specifier = next(iter(_UI_TYPE_MODULES))
    routes, import_errors, schema_notices = load_backend_routes()

    findings = _toolchain_findings(unparsed, import_errors, schema_notices, collisions)

    rows: list[Row] = []
    for call in calls:
        route = _match_route(routes, call)
        if route is None:
            # `api-surface.test.ts` owns "this path must exist", and it
            # drives the REAL client rather than parsing it. A miss here is
            # therefore most likely this parser's, so it is reported as the
            # parser's gap and not as a contract break — claiming otherwise
            # would duplicate a stronger check with a weaker one.
            rows.append(Row(call, "?", "-", "UNMATCHED", []))
            findings.append(
                Finding(
                    "path_unmatched",
                    call.client_method,
                    call.http,
                    call.path,
                    "no OpenAPI operation for this (method, path); "
                    "existence is owned by api-surface.test.ts — this row "
                    "is unaudited for SHAPE",
                )
            )
            continue
        # Resolve the TS name in the namespace of the module `apiClient.ts`
        # imports it from — the `Finding` collision makes this mandatory.
        root = _ts_element_type(call.ts_type)
        specifier = type_origin.get(root, default_specifier)
        if specifier not in namespaces:
            specifier = default_specifier
        interfaces, unions = namespaces[specifier]
        if route.schema is None and call.ts_type in _OPAQUE_TS_TYPES:
            # Both sides say "no body" — a 204 DELETE. Nothing to compare,
            # by design; distinguished from UNDECLARED so the residual
            # blind spot in the report is the real one.
            state = "NO_BODY"
        elif route.schema is None:
            state = "UNDECLARED"
        elif call.ts_type in _OPAQUE_TS_TYPES:
            state = "OPAQUE"
        elif _ts_element_type(call.ts_type) not in interfaces:
            # The response type is declared inline in `apiClient.ts`
            # (e.g. `LoginResponse`) rather than in one of the wire-type
            # modules, so there is no mirror to compare against. Moving
            # such a type into `lib/types.ts` is what brings its route
            # into the audit.
            state = "LOCAL"
        else:
            state = "COMPARED"
        row_findings = compare(call, route, interfaces, unions)
        row_findings.extend(compare_request(call, route, interfaces))
        findings.extend(row_findings)
        rows.append(
            Row(call, route.component, route.path, state, row_findings)
        )
    return Report(rows=rows, findings=findings)


def _print_rows(report: Report) -> None:
    print(
        f"{'client method':<34}{'verb':<7}{'c':<17}{'state':<11}response type"
    )
    print("-" * 104)
    for row in sorted(report.rows, key=lambda r: (r.call.client_method, r.call.http)):
        mark = "!" if row.findings else " "
        print(
            f"{mark}{row.call.client_method:<33}{row.call.http:<7}"
            f"{row.component:<17}{row.schema_state:<11}{row.call.ts_type}"
        )
    states: dict[str, int] = {}
    for row in report.rows:
        states[row.schema_state] = states.get(row.schema_state, 0) + 1
    print("-" * 104)
    print(f"{len(report.rows)} UI call sites")
    print("  response  " + "  ".join(
        f"{state}={count}" for state, count in sorted(states.items())
    ))
    print("  request   " + "  ".join(
        f"{kind}={count}" for kind, count in sorted(report.request_states.items())
    ) + f"  |  {report.query_named} calls name a query param")


def _print_findings(report: Report) -> None:
    if not report.findings:
        print("no findings")
        return
    by_code: dict[str, list[Finding]] = {}
    for finding in report.findings:
        by_code.setdefault(finding.code, []).append(finding)
    for code in sorted(by_code, key=lambda c: (c not in BLOCKING, c)):
        items = by_code[code]
        tag = "BLOCKING" if code in BLOCKING else "advisory"
        print(f"\n{code}  ({len(items)}, {tag})")
        for finding in items:
            print(f"  {finding.http:<7}{finding.path}")
            print(f"          {finding.detail}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero when a BLOCKING finding exists",
    )
    parser.add_argument("--findings", action="store_true", help="findings only")
    parser.add_argument("--json", action="store_true", help="machine-readable")
    args = parser.parse_args()

    report = audit()

    if args.json:
        print(
            json.dumps(
                {
                    "rows": [
                        {
                            "client_method": r.call.client_method,
                            "http": r.call.http,
                            "path": r.call.path,
                            "ts_type": r.call.ts_type,
                            "component": r.component,
                            "backend_path": r.backend_path,
                            "state": r.schema_state,
                        }
                        for r in report.rows
                    ],
                    "findings": [
                        {
                            "code": f.code,
                            "blocking": f.blocking,
                            "client_method": f.client_method,
                            "http": f.http,
                            "path": f.path,
                            "detail": f.detail,
                        }
                        for f in report.findings
                    ],
                },
                indent=2,
            )
        )
    elif args.findings:
        _print_findings(report)
    else:
        _print_rows(report)
        _print_findings(report)

    if args.check:
        blocking = report.blocking
        if blocking:
            print(f"\n{len(blocking)} blocking finding(s)")
            return 1
        print("\nno blocking finding")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
