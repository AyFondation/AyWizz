#!/usr/bin/env python3
# =============================================================================
# File: audit_openapi_docs.py
# Version: 1
# Path: ay_platform_core/scripts/checks/audit_openapi_docs.py
# Description: Scores the OpenAPI document each component generates, so
#              "the documentation is exhaustive" is a measurement rather
#              than an impression.
#
#              WHY IT EXISTS. The generated document was thin in ways
#              nobody could see without reading it: 268 of 272 operations
#              declared no 4xx at all, so a consumer could not learn that
#              an endpoint refuses them; 555 parameters advertised the
#              gateway-injected identity headers as caller-supplied, which
#              is the one thing `forward-auth-c2` exists to refuse; and
#              every app carried the default `version="0.1.0"` with no
#              description. None of that failed a test, because nothing in
#              the repository read the document.
#
#              WHAT IT CHECKS, and why each one is a defect rather than a
#              preference:
#                · `no_error_responses` — an operation with no 4xx. A
#                  consumer cannot distinguish "cannot fail" from "nobody
#                  wrote it down".
#                · `injected_header_exposed` — a gateway-injected identity
#                  header present as a request parameter. Documents an
#                  attack as an instruction.
#                · `role_gated_without_403` — the auth-matrix catalogue
#                  says this route is `ROLE_GATED` and the document does
#                  not say it can 403. The catalogue is the authority here
#                  precisely because `_require_role(...)` runs in the
#                  handler BODY and so cannot be introspected from the
#                  route; the audit is where that knowledge is applied,
#                  rather than production code importing tests.
#                · `no_summary` / `no_description` / `no_tags` — an
#                  operation a reader cannot place.
#                · `undescribed_field` — a request or response field with
#                  no prose. Counted, not listed: there are over a
#                  thousand, and a list that long is not actionable.
#
#              THE RATCHETS, not absolutes. Field prose is being written in
#              batches, so the gate pins the CURRENT state and refuses
#              regression — the same shape as
#              `test_project_role_gate_ratchet.py`. A number that can only
#              improve is worth more than a target nobody meets.
#
# Usage:
#   python ay_platform_core/scripts/checks/audit_openapi_docs.py
#   python ay_platform_core/scripts/checks/audit_openapi_docs.py --findings
#   python ay_platform_core/scripts/checks/audit_openapi_docs.py --check
#   python ay_platform_core/scripts/checks/audit_openapi_docs.py --json
# =============================================================================

from __future__ import annotations

import argparse
import importlib
import json
import logging
import re
import sys
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve()
_SUBPROJECT_ROOT = _HERE.parents[2]
sys.path.insert(0, str(_SUBPROJECT_ROOT))

from tests.e2e.auth_matrix._catalog import ENDPOINTS, Auth  # noqa: E402

from ay_platform_core.api_docs import GATEWAY_INJECTED_HEADERS  # noqa: E402

#: The components serving HTTP. Mirrors `audit_ui_api_chain._HTTP_COMPONENTS`;
#: each name is also the `COMPONENT_MODULE` its Deployment sets.
HTTP_COMPONENTS: tuple[str, ...] = (
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

_VERBS = frozenset({"get", "post", "put", "patch", "delete"})

#: An operation with none of these says nothing about how it refuses.
_ERROR_CODES = frozenset({"400", "401", "403", "404", "409", "410", "412", "423"})

#: Schemas FastAPI emits itself. Their fields are not ours to describe, and
#: counting them made the undescribed-field number read six higher than the
#: work it actually represents — a metric that cannot reach zero teaches
#: the reader to ignore it.
_FRAMEWORK_SCHEMAS: frozenset[str] = frozenset(
    {"HTTPValidationError", "ValidationError"}
)

#: Operations that legitimately declare no 4xx: genuinely public, no path
#: parameter, nothing to refuse. Enumerated rather than pattern-matched so
#: a tenth one has to be a decision.
_NO_REFUSAL_POSSIBLE: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/health"),
        ("GET", "/metrics"),
        ("GET", "/auth/config"),
        ("GET", "/ux/config"),
    }
)


@dataclass(frozen=True)
class Finding:
    code: str
    component: str
    http: str
    path: str
    detail: str

    @property
    def blocking(self) -> bool:
        return self.code in BLOCKING


#: Blocking: each is a statement the document MAKES that is wrong, or one a
#: consumer needs and cannot get at all. Omissions are counted and
#: ratcheted instead — see this module's header.
#:
#: `403_on_ungated_route` is here and `role_gated_without_403` is not, and
#: the asymmetry is the point: a 403 the route cannot produce is a FALSE
#: statement, and a reader has no way to tell it is false. A missing 403 is
#: an omission — the reader learns less than they could, but nothing they
#: learn is wrong. Shipping the first is worse than shipping the second.
BLOCKING = frozenset(
    {
        "injected_header_exposed",
        "no_summary",
        "no_tags",
        "403_on_ungated_route",
    }
)


@dataclass
class Report:
    operations: int = 0
    fields: int = 0
    undescribed_fields: int = 0
    findings: list[Finding] = field(default_factory=list)
    apps_without_description: list[str] = field(default_factory=list)
    import_errors: list[str] = field(default_factory=list)

    @property
    def blocking(self) -> list[Finding]:
        return [f for f in self.findings if f.blocking]

    def count(self, code: str) -> int:
        return sum(1 for f in self.findings if f.code == code)


def _normalise_path(path: str) -> str:
    """Strip FastAPI's converter suffix: `{path:path}` → `{path}`.

    The catalogue stores the DECLARED path, converter included; OpenAPI
    renders the parameter name alone. Comparing them raw reported seven
    catch-all routes as "the document claims a 403 the catalogue says is
    un-gated" — all seven genuinely gated, the mismatch entirely in the
    spelling. A finding that says the contract is wrong when the
    comparison is wrong costs more than no finding at all.
    """
    return re.sub(r"\{([^}:]+):[^}]+\}", r"{\1}", path)


def _role_gated_routes() -> set[tuple[str, str]]:
    """`(method, path)` of every route the auth-matrix catalogue gates.

    The catalogue is pinned to the live routers by
    `tests/coherence/test_route_catalog.py`, so this is not a second
    opinion about which routes exist — it is the same one, carrying the
    gate information the route object does not expose.
    """
    return {
        (e.method.upper(), _normalise_path(e.path))
        for e in ENDPOINTS
        if e.auth == Auth.ROLE_GATED
    }


def _silence_component_logging() -> None:
    """Keep component bootstrap logs off stdout.

    Importing a component's `main` module emits structured JSON log lines
    on STDOUT (`AY_SECRET_MASTER_KEY absent`, embedding fallbacks, …). That
    made `--json` unparseable — the whole point of the flag — because the
    document came out after six log objects. Silencing is correct here
    rather than merely convenient: this script reads a schema, it does not
    exercise a runtime, so those warnings describe a condition that has no
    bearing on what it reports.
    """
    logging.disable(logging.CRITICAL)


def audit() -> Report:
    _silence_component_logging()
    report = Report()
    gated = _role_gated_routes()

    for component in HTTP_COMPONENTS:
        module_name = f"ay_platform_core.{component}.main"
        try:
            module = importlib.import_module(module_name)
            # Local filter: `pyproject.toml` sets `filterwarnings = error`,
            # and a schema warning raised as an exception would drop a whole
            # component out of the audit while every assertion still passed.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                spec = module.app.openapi()
        except Exception as exc:  # reported as a finding, never swallowed
            report.import_errors.append(
                f"{module_name}: {type(exc).__name__}: {exc}"
            )
            continue

        if not (spec.get("info") or {}).get("description"):
            report.apps_without_description.append(component)

        for raw_path, operations in (spec.get("paths") or {}).items():
            for verb, operation in operations.items():
                if verb.lower() not in _VERBS or not isinstance(operation, dict):
                    continue
                report.operations += 1
                _check_operation(
                    report, component, verb.upper(), raw_path, operation, gated
                )

        schemas = (spec.get("components") or {}).get("schemas") or {}
        for name, schema in schemas.items():
            if name in _FRAMEWORK_SCHEMAS:
                continue
            for prop in (schema.get("properties") or {}).values():
                if not isinstance(prop, dict):
                    continue
                report.fields += 1
                if not prop.get("description"):
                    report.undescribed_fields += 1

    return report


def _check_operation(
    report: Report,
    component: str,
    http: str,
    path: str,
    operation: dict[str, Any],
    gated: set[tuple[str, str]],
) -> None:
    def add(code: str, detail: str) -> None:
        report.findings.append(Finding(code, component, http, path, detail))

    for param in operation.get("parameters") or []:
        if (
            isinstance(param, dict)
            and param.get("in") == "header"
            and str(param.get("name", "")).lower() in GATEWAY_INJECTED_HEADERS
        ):
            add(
                "injected_header_exposed",
                f"`{param['name']}` is injected by C1 after forward-auth; "
                "documenting it as a request parameter tells a reader to "
                "forge the header the gateway strips",
            )

    if not operation.get("summary"):
        add("no_summary", "no summary — the operation list reads as a path dump")
    if not operation.get("tags"):
        add("no_tags", "no tag — the operation appears in no group")
    if not operation.get("description"):
        add("no_description", "no description (the handler has no docstring)")

    codes = set(operation.get("responses") or {})
    if not (codes & _ERROR_CODES) and (http, path) not in _NO_REFUSAL_POSSIBLE:
        add("no_error_responses", "declares no 4xx: how it refuses is undocumented")

    if (http, path) in gated and "403" not in codes:
        add(
            "role_gated_without_403",
            "the auth-matrix catalogue declares this route ROLE_GATED and "
            "the document does not say it can 403",
        )
    # The INVERSE, and it matters as much. `ROLE_GATED_RESPONSES` is
    # attached per ROUTER, so a router holding one ungated route would
    # make the document claim a refusal that route cannot produce — a
    # false statement, which is worse than a missing one because a reader
    # cannot tell it is false. This is the referee for applying the 403
    # broadly: over-claiming shows up here instead of going unnoticed.
    if "403" in codes and (http, path) not in gated:
        add(
            "403_on_ungated_route",
            "the document says this route can 403 and the auth-matrix "
            "catalogue declares it un-gated — either the catalogue row is "
            "stale or the 403 is over-claimed",
        )


def _print(report: Report, findings_only: bool) -> None:
    if not findings_only:
        print(f"{report.operations} operations across {len(HTTP_COMPONENTS)} components")
        print(
            f"{report.fields} schema fields, "
            f"{report.undescribed_fields} without a description"
        )
        print(
            "apps without a description: "
            + (", ".join(report.apps_without_description) or "none")
        )
        print()
    by_code: dict[str, list[Finding]] = {}
    for finding in report.findings:
        by_code.setdefault(finding.code, []).append(finding)
    if not by_code and not report.import_errors:
        print("no findings")
        return
    for note in report.import_errors:
        print(f"component_unreadable  {note}")
    for code in sorted(by_code, key=lambda c: (c not in BLOCKING, c)):
        items = by_code[code]
        tag = "BLOCKING" if code in BLOCKING else "counted"
        print(f"{code}  ({len(items)}, {tag})")
        for finding in items[:12]:
            print(f"  {finding.component:<16}{finding.http:<7}{finding.path}")
        if len(items) > 12:
            print(f"  … and {len(items) - 12} more")
        print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit non-zero on a blocking finding")
    parser.add_argument("--findings", action="store_true", help="findings only")
    parser.add_argument("--json", action="store_true", help="machine-readable")
    args = parser.parse_args()

    report = audit()

    if args.json:
        print(
            json.dumps(
                {
                    "operations": report.operations,
                    "fields": report.fields,
                    "undescribed_fields": report.undescribed_fields,
                    "apps_without_description": report.apps_without_description,
                    "import_errors": report.import_errors,
                    "findings": [
                        {
                            "code": f.code,
                            "blocking": f.blocking,
                            "component": f.component,
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
    else:
        _print(report, findings_only=args.findings)

    if args.check:
        if report.blocking or report.import_errors:
            print(f"\n{len(report.blocking)} blocking finding(s)")
            return 1
        print("\nno blocking finding")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
