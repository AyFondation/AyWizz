# =============================================================================
# File: test_code_extractor.py
# Version: 1
# Path: ay_platform_core/tests/unit/c7_memory/test_code_extractor.py
# Description: Unit tests for the Python code-domain L1 structural extractor
#              (V2 #3-A.a / R-400-200, R-400-201, D-004, E-400-006). Real
#              tree-sitter (no mocks) over in-memory source :
#                - MODULE / CLASS / FUNCTION / METHOD entities ;
#                - DEFINES / IMPORTS / INHERITS_FROM relations ;
#                - @relation markers → IMPLEMENTS / VALIDATES / DERIVES_FROM
#                  (out-of-ontology targets skipped) ;
#                - determinism + EXTRACTED/L1 defaults + error tolerance.
# =============================================================================

from __future__ import annotations

import pytest

from ay_platform_core.c7_memory.kg.code_extractor import extract_structural_python
from ay_platform_core.c7_memory.models import Provenance

pytestmark = pytest.mark.unit

_SRC = '''# @relation implements:R-400-200
# @relation validates:T-400-001
# @relation derives-from:E-400-006
import os
from a.b import c
import x.y as z


class Foo(Base, Mixin):
    def method_one(self):
        return os.getcwd()

    def method_two(self):
        pass


def top_level():
    return 1
'''


def _extract() -> tuple[dict[str, str], set[tuple[str, str, str]]]:
    result = extract_structural_python(_SRC, module_name="pkg.mod")
    entities = {e.name: str(e.type) for e in result.entities}
    edges = {
        (r.subject.name, str(r.type), r.object.name) for r in result.relations
    }
    return entities, edges


class TestEntities:
    def test_module_class_function_method_entities(self) -> None:
        entities, _ = _extract()
        assert entities["pkg.mod"] == "MODULE"
        assert entities["pkg.mod.Foo"] == "CLASS"
        assert entities["pkg.mod.Foo.method_one"] == "METHOD"
        assert entities["pkg.mod.Foo.method_two"] == "METHOD"
        assert entities["pkg.mod.top_level"] == "FUNCTION"

    def test_imported_modules_are_module_entities(self) -> None:
        entities, _ = _extract()
        assert entities["os"] == "MODULE"
        assert entities["a.b"] == "MODULE"
        assert entities["x.y"] == "MODULE"  # `import x.y as z` → module is x.y

    def test_extracted_provenance_and_layer(self) -> None:
        result = extract_structural_python(_SRC, module_name="pkg.mod")
        assert all(e.provenance is Provenance.EXTRACTED for e in result.entities)
        assert all(e.layer == "L1" for e in result.entities)
        assert all(e.confidence == 1.0 for e in result.relations)


class TestRelations:
    def test_defines_edges(self) -> None:
        _, edges = _extract()
        assert ("pkg.mod", "DEFINES", "pkg.mod.Foo") in edges
        assert ("pkg.mod", "DEFINES", "pkg.mod.top_level") in edges
        assert ("pkg.mod.Foo", "DEFINES", "pkg.mod.Foo.method_one") in edges

    def test_import_edges(self) -> None:
        _, edges = _extract()
        assert ("pkg.mod", "IMPORTS", "os") in edges
        assert ("pkg.mod", "IMPORTS", "a.b") in edges
        assert ("pkg.mod", "IMPORTS", "x.y") in edges

    def test_inherits_from_edges(self) -> None:
        _, edges = _extract()
        assert ("pkg.mod.Foo", "INHERITS_FROM", "Base") in edges
        assert ("pkg.mod.Foo", "INHERITS_FROM", "Mixin") in edges

    def test_relation_marker_edges(self) -> None:
        _, edges = _extract()
        assert ("pkg.mod", "IMPLEMENTS", "R-400-200") in edges
        assert ("pkg.mod", "VALIDATES", "T-400-001") in edges
        # `derives-from:E-400-006` — E has no ontology slot → skipped.
        assert not any(r[2] == "E-400-006" for r in edges)

    def test_calls_edges(self) -> None:
        # method_one calls os.getcwd() ; the CALLS edge runs method → callee.
        _, edges = _extract()
        assert ("pkg.mod.Foo.method_one", "CALLS", "os.getcwd") in edges
        # method_two / top_level make no calls → no spurious CALLS edges.
        assert not any(
            r[0] == "pkg.mod.Foo.method_two" and r[1] == "CALLS" for r in edges
        )


class TestRobustness:
    def test_empty_source_yields_only_module(self) -> None:
        result = extract_structural_python("", module_name="empty.mod")
        assert [e.name for e in result.entities] == ["empty.mod"]
        assert result.relations == []

    def test_malformed_source_does_not_crash(self) -> None:
        # tree-sitter is error-tolerant — a broken file SHALL NOT raise ;
        # it yields at least the MODULE entity.
        result = extract_structural_python(
            "def (:\n  class ???", module_name="broken.mod"
        )
        assert any(e.name == "broken.mod" for e in result.entities)

    def test_deterministic(self) -> None:
        first = extract_structural_python(_SRC, module_name="pkg.mod")
        second = extract_structural_python(_SRC, module_name="pkg.mod")
        assert [e.model_dump() for e in first.entities] == [
            e.model_dump() for e in second.entities
        ]
        assert {(r.subject.name, r.type, r.object.name) for r in first.relations} == {
            (r.subject.name, r.type, r.object.name) for r in second.relations
        }
