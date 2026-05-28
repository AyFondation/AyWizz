# =============================================================================
# File: code_extractor.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c7_memory/kg/code_extractor.py
# Description: Deterministic schema-guided L1 structural extractor for the
#              `code` domain — Python (V2 #3-A.a / R-400-200, R-400-201,
#              D-004, E-400-006). Companion to `structural_extractor.py`
#              (requirements corpus) ; both feed the SAME closed ontology.
#
#              Uses tree-sitter (D-004) via `tree-sitter-language-pack` to
#              parse Python source into an AST, then maps it onto the closed
#              ontology — entities MODULE / CLASS / FUNCTION / METHOD ;
#              relations DEFINES / IMPORTS / INHERITS_FROM — plus the
#              `@relation implements:/validates:/derives-from:` comment
#              markers (→ IMPLEMENTS / VALIDATES / DERIVES_FROM edges from the
#              module to the referenced spec entity, sharing the C6
#              traceability vocabulary). All records are `EXTRACTED` /
#              confidence 1.0.
#
#              Determinism : same source → same graph (deduplicated by entity
#              name / (subject, type, object) triple). It needs the ORIGINAL
#              source bytes (Python indentation is significant) — NOT the
#              whitespace-collapsed chunk text, so the service feeds it the
#              raw blob (`get_source_blob`), not reconstructed chunks.
#
#              Scope : top-level classes/functions/imports + one level of
#              methods inside a class body + CALLS edges (best-effort, the
#              callee text used as-is, not descending into nested scopes).
#              Other languages (TS/YAML/MD, available in the language pack)
#              are a follow-on increment.
# =============================================================================

from __future__ import annotations

import functools
import re
from typing import Any

from tree_sitter_language_pack import get_parser

from ay_platform_core.c7_memory.kg.ontology import (
    RelationType,
    StructuralEntity,
    StructuralExtraction,
    StructuralRelation,
    entity_for_spec_id,
)


# Lazily-initialised, cached parser. `get_parser` does filesystem IO
# (tree-sitter-language-pack's Rust core touches a grammar cache), which fails
# under a container user without a writable HOME (`Dockerfile.api` creates the
# `app` user with `--no-create-home`) → `IO error: Permission denied`. Loading
# it at IMPORT time would crash the whole C7 service (and transitively C3,
# which imports `c7_memory.service`) at boot. A single OPTIONAL feature
# (code-AST extraction) must never prevent the service from starting, so the
# load is deferred to first use. Typed `Any` on purpose : the installed Rust
# binding exposes node accessors as METHODS (`kind()`, `start_byte()`,
# `root_node()`), which the official property-based stubs do not match —
# treating nodes as `Any` avoids that mismatch.
@functools.cache
def _python_parser() -> Any:
    """Return the cached tree-sitter Python parser, loading it on first use.
    Any environment failure (e.g. no writable HOME for the grammar cache)
    surfaces here — when code extraction is actually invoked — never at
    import, so it cannot take the service down at boot."""
    return get_parser("python")

_RELATION_MARKER_RE = re.compile(
    r"@relation\s+(?P<verb>implements|validates|derives-from):"
    r"(?P<id>(?:R|E|D|T)-[A-Z0-9-]+)"
)
_MARKER_VERB_TO_RELATION: dict[str, RelationType] = {
    "implements": "IMPLEMENTS",
    "validates": "VALIDATES",
    "derives-from": "DERIVES_FROM",
}


def _text(node: Any, src: bytes) -> str:
    return src[node.start_byte() : node.end_byte()].decode("utf-8", "replace")


def _named(node: Any) -> list[Any]:
    return [node.named_child(i) for i in range(node.named_child_count())]


def _unwrap(node: Any) -> Any:
    """A `decorated_definition` wraps the real class/function node — unwrap it
    so a decorated class/method is still classified correctly."""
    if node.kind() == "decorated_definition":
        for child in _named(node):
            if child.kind() in ("class_definition", "function_definition"):
                return child
    return node


def _def_name(node: Any, src: bytes) -> str:
    name_node = node.child_by_field_name("name")
    return _text(name_node, src) if name_node is not None else ""


def _imported_modules(node: Any, src: bytes) -> list[str]:
    """Module paths of an `import a, b.c` / `import a.b as z` statement."""
    modules: list[str] = []
    for child in _named(node):
        if child.kind() == "dotted_name":
            modules.append(_text(child, src))
        elif child.kind() == "aliased_import":
            inner = child.named_child(0)  # the dotted_name, before the alias
            if inner is not None:
                modules.append(_text(inner, src))
    return modules


def _callee_names(scope: Any, src: bytes) -> list[str]:
    """Callee names of every `call` in `scope`'s subtree, NOT descending into
    nested function/class definitions (those are separate scopes). Best-effort
    L1 : the callee text (`helper`, `os.getcwd`, `self.render`) is used as-is."""
    names: list[str] = []
    stack: list[Any] = _named(scope)
    while stack:
        n = stack.pop()
        kind = n.kind()
        if kind in ("function_definition", "class_definition"):
            continue  # separate scope — its calls aren't this caller's
        if kind == "call":
            fn = n.child_by_field_name("function")
            if fn is not None and fn.kind() in ("identifier", "attribute"):
                names.append(_text(fn, src))
        stack.extend(_named(n))
    return names


def _emit_calls(caller: StructuralEntity, def_node: Any, src: bytes, add_rel: Any) -> None:
    """Emit CALLS edges from `caller` to each function it calls in its body."""
    body = def_node.child_by_field_name("body")
    if body is None:
        return
    for callee in _callee_names(body, src):
        add_rel(caller, "CALLS", StructuralEntity(name=callee, type="FUNCTION"))


def extract_structural_python(
    text: str, *, module_name: str
) -> StructuralExtraction:
    """Extract the L1 code graph from one Python source. `module_name` names
    the MODULE entity (the file's logical id) ; classes/functions/methods are
    qualified under it (`<module>.Foo`, `<module>.Foo.method`). Deterministic
    and deduplicated."""
    src = text.encode("utf-8")
    root = _python_parser().parse(text).root_node()

    entities: dict[str, StructuralEntity] = {}
    relations: dict[tuple[str, str, str], StructuralRelation] = {}

    module = StructuralEntity(name=module_name, type="MODULE")
    entities[module.name] = module

    def _add_rel(
        subject: StructuralEntity, rtype: RelationType, obj: StructuralEntity
    ) -> None:
        entities.setdefault(subject.name, subject)
        entities.setdefault(obj.name, obj)
        relations[(subject.name, rtype, obj.name)] = StructuralRelation(
            subject=subject, type=rtype, object=obj
        )

    for top in _named(root):
        node = _unwrap(top)
        kind = node.kind()
        if kind == "import_statement":
            for mod in _imported_modules(node, src):
                _add_rel(module, "IMPORTS", StructuralEntity(name=mod, type="MODULE"))
        elif kind == "import_from_statement":
            mn = node.child_by_field_name("module_name")
            if mn is not None:
                _add_rel(
                    module, "IMPORTS",
                    StructuralEntity(name=_text(mn, src), type="MODULE"),
                )
        elif kind == "function_definition":
            name = _def_name(node, src)
            if name:
                func = StructuralEntity(name=f"{module_name}.{name}", type="FUNCTION")
                _add_rel(module, "DEFINES", func)
                _emit_calls(func, node, src, _add_rel)
        elif kind == "class_definition":
            _extract_class(node, module, module_name, src, _add_rel)

    for marker in _RELATION_MARKER_RE.finditer(text):
        obj = entity_for_spec_id(marker.group("id"))
        if obj is None:
            continue  # out-of-ontology target (e.g. E-NNN) — skip
        _add_rel(module, _MARKER_VERB_TO_RELATION[marker.group("verb")], obj)

    return StructuralExtraction(
        entities=list(entities.values()), relations=list(relations.values())
    )


def _extract_class(
    node: Any,
    module: StructuralEntity,
    module_name: str,
    src: bytes,
    add_rel: Any,
) -> None:
    cls_name = _def_name(node, src)
    if not cls_name:
        return
    cls = StructuralEntity(name=f"{module_name}.{cls_name}", type="CLASS")
    add_rel(module, "DEFINES", cls)

    superclasses = node.child_by_field_name("superclasses")
    if superclasses is not None:
        for arg in _named(superclasses):
            # Skip keyword args (e.g. metaclass=…) — only positional bases.
            if arg.kind() in ("identifier", "attribute", "dotted_name"):
                add_rel(
                    cls, "INHERITS_FROM",
                    StructuralEntity(name=_text(arg, src), type="CLASS"),
                )

    body = node.child_by_field_name("body")
    if body is not None:
        for stmt in _named(body):
            member = _unwrap(stmt)
            if member.kind() == "function_definition":
                method_name = _def_name(member, src)
                if method_name:
                    method = StructuralEntity(
                        name=f"{module_name}.{cls_name}.{method_name}",
                        type="METHOD",
                    )
                    add_rel(cls, "DEFINES", method)
                    _emit_calls(method, member, src, add_rel)
