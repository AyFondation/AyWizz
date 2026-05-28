#!/usr/bin/env python3
# =============================================================================
# File: audit_implementation_status.py
# Version: 1
# Path: ay_platform_core/scripts/checks/audit_implementation_status.py
# Description: Audit script — cross-references every R-NNN-XXX requirement
#              declared in `requirements/*-SPEC*.md` with `@relation
#              implements:R-NNN-XXX` markers in `src/` and
#              `@relation validates:R-NNN-XXX` markers in `tests/`. Emits
#              a markdown report grouped by spec, with a status per
#              requirement: `implemented`, `partial`, `not-yet`,
#              `divergent` (status=approved/draft but no marker found).
#
# Usage:
#   python ay_platform_core/scripts/checks/audit_implementation_status.py \
#       [--write requirements/060-IMPLEMENTATION-STATUS.md]
#
# When --write is omitted the report goes to stdout.
# =============================================================================

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
REQUIREMENTS = ROOT / "requirements"
SRC = ROOT / "ay_platform_core" / "src"
TESTS = ROOT / "ay_platform_core" / "tests"
INFRA = ROOT / "infra"
GH_WORKFLOWS = ROOT / ".github" / "workflows"

# (root, glob extensions) pairs — `@relation implements:` markers may
# live in Python source, but also in compose YAML, Dockerfiles, CI
# workflows, and shell scripts. Each root is scanned ONLY for the
# extensions appropriate to it.
_IMPL_SCAN_TARGETS: list[tuple[Path, tuple[str, ...]]] = [
    (SRC, ("*.py",)),
    # `*.json` covers e.g. n8n workflow definitions that carry their
    # marker in a `_comment` field.
    (INFRA, ("*.yml", "*.yaml", "*.sh", "*.json", "Dockerfile*")),
    (GH_WORKFLOWS, ("*.yml", "*.yaml")),
    # The test stack's compose file is infra-of-test ; its markers
    # describe the deployable topology requirements (R-100-115/117/119/122).
    (TESTS, ("*.yml", "*.yaml", "Dockerfile*")),
]

_R_ID_RE = re.compile(r"^id:\s*(R-\d+-\d+)\s*$", re.MULTILINE)
_VERSION_RE = re.compile(r"^version:\s*(\d+)\s*$", re.MULTILINE)
_STATUS_RE = re.compile(r"^status:\s*(\w+)\s*$", re.MULTILINE)
# A `@relation implements:` / `@relation validates:` marker may list MORE
# than one requirement id on the same line, separated by spaces (or
# commas). Two-stage parse: locate the marker line, then enumerate
# every R-NNN-XXX inside the rest of the line.
_RELATION_LINE_RE = re.compile(
    r"@relation\s+(implements|validates):\s*([^\n]+)"
)
_R_ID_INLINE_RE = re.compile(r"R-\d+-\d+")


@dataclass
class Requirement:
    rid: str
    spec_file: Path
    version: int
    status: str
    implementing_files: set[Path] = field(default_factory=set)
    validating_files: set[Path] = field(default_factory=set)

    @property
    def kind(self) -> str:
        # R-100-* / R-200-* / etc → derive the spec family
        return self.rid.split("-")[1] if self.rid.startswith("R-") else ""

    @property
    def overall_status(self) -> str:
        impl = bool(self.implementing_files)
        test = bool(self.validating_files)
        if impl and test:
            return "tested"
        if impl and not test:
            return "implemented"
        if not impl and test:
            return "test-only"  # rare, suspicious
        # No markers at all.
        if self.status == "approved":
            return "divergent"
        return "not-yet"


def _parse_yaml_block(block: str, regex: re.Pattern) -> str | None:
    m = regex.search(block)
    return m.group(1) if m else None


def _extract_requirements(spec_path: Path) -> list[Requirement]:
    """Walk the spec file, locate each ```yaml block``` and read id/version/status."""
    text = spec_path.read_text(encoding="utf-8")
    blocks = re.findall(r"```yaml\s*\n(.*?)\n```", text, flags=re.DOTALL)
    out: list[Requirement] = []
    for block in blocks:
        rid = _parse_yaml_block(block, _R_ID_RE)
        if rid is None:
            continue
        version = int(_parse_yaml_block(block, _VERSION_RE) or "1")
        status = _parse_yaml_block(block, _STATUS_RE) or "draft"
        out.append(Requirement(rid=rid, spec_file=spec_path, version=version, status=status))
    return out


def _scan_relations(
    targets: list[tuple[Path, tuple[str, ...]]],
    *,
    relation_kind: str,  # "implements" or "validates"
) -> dict[str, set[Path]]:
    """Walk every (root, extensions) target, collect file paths per
    requirement id referenced in markers.

    Multi-id markers are supported: `@relation implements:R-100-001 R-100-002`
    counts the file as implementing BOTH requirements.
    """
    out: dict[str, set[Path]] = defaultdict(set)
    for root, extensions in targets:
        if not root.exists():
            continue
        for ext in extensions:
            for path in root.rglob(ext):
                if "__pycache__" in path.parts or ".git" in path.parts:
                    continue
                try:
                    content = path.read_text(encoding="utf-8")
                except Exception:
                    continue
                for match in _RELATION_LINE_RE.finditer(content):
                    if match.group(1) != relation_kind:
                        continue
                    for rid in _R_ID_INLINE_RE.findall(match.group(2)):
                        out[rid].add(path)
    return out


def _format_path(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def _format_files(paths: set[Path], limit: int = 3) -> str:
    sorted_paths = sorted(paths, key=lambda p: str(p))
    rendered = ", ".join(f"`{_format_path(p)}`" for p in sorted_paths[:limit])
    if len(sorted_paths) > limit:
        rendered += f" (+{len(sorted_paths) - limit} more)"
    return rendered or "—"


def render_report(requirements: list[Requirement]) -> str:
    """Group by spec family (R-100, R-200, …) and render one markdown table each."""
    grouped: dict[str, list[Requirement]] = defaultdict(list)
    for r in requirements:
        grouped[r.kind].append(r)

    spec_titles = {
        "100": "100-SPEC-ARCHITECTURE",
        "200": "200-SPEC-PIPELINE-AGENT",
        "300": "300-SPEC-REQUIREMENTS-MGMT",
        "400": "400-SPEC-MEMORY-RAG",
        "500": "500-SPEC-UI-UX",
        "600": "600-SPEC-CODE-QUALITY",
        "700": "700-SPEC-VERTICAL-COHERENCE",
        "800": "800-SPEC-LLM-ABSTRACTION",
    }

    # Aggregate counts
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in requirements:
        counts[r.kind][r.overall_status] += 1

    lines: list[str] = []
    lines.append("---")
    lines.append("document: 060-IMPLEMENTATION-STATUS")
    lines.append("version: 1")
    lines.append("path: requirements/060-IMPLEMENTATION-STATUS.md")
    lines.append("language: en")
    lines.append("status: draft")
    lines.append("audience: any-fresh-session, contributor-onboarding")
    lines.append("generated-by: ay_platform_core/scripts/checks/audit_implementation_status.py")
    lines.append("---")
    lines.append("")
    lines.append("# Implementation Status — cross-reference of R-* requirements vs. code")
    lines.append("")
    lines.append("> **Generated** — re-run the audit script to refresh this file. The")
    lines.append("> mapping is mechanical: it counts `@relation implements:R-…`")
    lines.append("> markers in `ay_platform_core/src/` and `@relation validates:R-…`")
    lines.append("> markers in `ay_platform_core/tests/`. Status legend:")
    lines.append(">")
    lines.append("> - `tested`: at least one implementer + at least one validating test.")
    lines.append("> - `implemented`: at least one implementer, no `@relation validates:` marker.")
    lines.append(">   (May still be tested via positional / functional tests — the marker")
    lines.append(">   is a stronger guarantee than coverage of the code path.)")
    lines.append("> - `test-only`: tests reference the requirement but no source file does.")
    lines.append(">   Three legitimate sub-cases (do NOT need fixing):")
    lines.append(">    - **Architectural meta-rules** (e.g. R-100-001 SRP, R-100-002 footprint) —")
    lines.append(">      no single file implements them; the project structure as a whole does.")
    lines.append(">    - **Test-as-implementation** (e.g. R-100-113 env coherence) — the test IS the")
    lines.append(">      mechanism that enforces the requirement; the marker on the test is the implem.")
    lines.append(">    - **WIP stubs** (e.g. R-300-080 import endpoint) — `status: draft` ; the impl is")
    lines.append(">      a 501 stub validated by tests. Will move to `tested` once the v2 work lands.")
    lines.append(">   The fourth case — stale marker after impl deletion — is what an audit catches.")
    lines.append("> - `divergent`: requirement is `status: approved` in the spec, but **no**")
    lines.append(">   marker exists in the codebase. Either the impl forgot the marker or")
    lines.append(">   the requirement is unimplemented despite being approved.")
    lines.append("> - `not-yet`: requirement is `status: draft`, no marker. Expected for v2 work.")
    lines.append("")

    # Summary table
    lines.append("## Summary")
    lines.append("")
    lines.append("| Spec | Total | tested | implemented | test-only | divergent | not-yet |")
    lines.append("|---|---|---|---|---|---|---|")
    overall_counts: dict[str, int] = defaultdict(int)
    for kind in sorted(grouped):
        title = spec_titles.get(kind, kind)
        c = counts[kind]
        total = sum(c.values())
        lines.append(
            f"| [{title}](./{title}.md) | {total} "
            f"| {c.get('tested', 0)} "
            f"| {c.get('implemented', 0)} "
            f"| {c.get('test-only', 0)} "
            f"| {c.get('divergent', 0)} "
            f"| {c.get('not-yet', 0)} |"
        )
        for k, v in c.items():
            overall_counts[k] += v
    overall_total = sum(overall_counts.values())
    lines.append(
        f"| **Total** | **{overall_total}** "
        f"| **{overall_counts.get('tested', 0)}** "
        f"| **{overall_counts.get('implemented', 0)}** "
        f"| **{overall_counts.get('test-only', 0)}** "
        f"| **{overall_counts.get('divergent', 0)}** "
        f"| **{overall_counts.get('not-yet', 0)}** |"
    )
    lines.append("")

    # Per-spec detail table
    for kind in sorted(grouped):
        title = spec_titles.get(kind, kind)
        lines.append(f"## R-{kind}-* — [{title}](./{title}.md)")
        lines.append("")
        lines.append("| ID | v | status | overall | implementing | validating |")
        lines.append("|---|---|---|---|---|---|")
        for r in sorted(grouped[kind], key=lambda x: x.rid):
            lines.append(
                f"| `{r.rid}` | v{r.version} | {r.status} | "
                f"**{r.overall_status}** "
                f"| {_format_files(r.implementing_files)} "
                f"| {_format_files(r.validating_files)} |"
            )
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("**End of 060-IMPLEMENTATION-STATUS.md.**")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        type=Path,
        default=None,
        help="Path to write the report. If omitted, write to stdout.",
    )
    parser.add_argument(
        "--fail-on-divergent",
        action="store_true",
        help=(
            "Exit non-zero if any approved requirement has no implementer "
            "marker. Useful for CI."
        ),
    )
    args = parser.parse_args()

    # 1. Parse all spec files
    spec_files = sorted(REQUIREMENTS.glob("*-SPEC-*.md"))
    requirements: list[Requirement] = []
    for spec in spec_files:
        requirements.extend(_extract_requirements(spec))

    # 2. Scan markers — multi-target so YAML / Dockerfile / shell
    # markers in infra, CI, and the test compose file are picked up
    # alongside Python sources.
    impls = _scan_relations(_IMPL_SCAN_TARGETS, relation_kind="implements")
    validates = _scan_relations(
        [(TESTS, ("*.py",))], relation_kind="validates"
    )
    # `@relation implements:` in Python tests is also valid (some
    # tests use it to mean "this test file IS the implementation").
    test_impls = _scan_relations(
        [(TESTS, ("*.py",))], relation_kind="implements"
    )

    # 3. Decorate requirements
    by_id: dict[str, Requirement] = {r.rid: r for r in requirements}
    for rid, files in impls.items():
        if rid in by_id:
            by_id[rid].implementing_files |= files
    for rid, files in validates.items():
        if rid in by_id:
            by_id[rid].validating_files |= files
    for rid, files in test_impls.items():
        if rid in by_id:
            by_id[rid].validating_files |= files

    # 4. Render
    report = render_report(requirements)

    if args.write:
        args.write.write_text(report, encoding="utf-8")
        print(f"wrote {args.write} ({len(requirements)} requirements)")
    else:
        print(report)

    if args.fail_on_divergent:
        divergent = [r for r in requirements if r.overall_status == "divergent"]
        if divergent:
            print(
                f"\nFAIL: {len(divergent)} approved requirement(s) without "
                f"implementer marker:",
                file=sys.stderr,
            )
            for r in divergent:
                print(f"  {r.rid} ({r.spec_file.name})", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
