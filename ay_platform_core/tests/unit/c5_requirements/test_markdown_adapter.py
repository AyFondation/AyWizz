# =============================================================================
# File: test_markdown_adapter.py
# Version: 1
# Path: ay_platform_core/tests/unit/c5_requirements/test_markdown_adapter.py
# Description: Unit tests for the C5 Markdown+YAML adapter — parse, validate,
#              round-trip (R-300-001, R-300-004, R-300-005).
# =============================================================================

from __future__ import annotations

import pytest

from ay_platform_core.c5_requirements.adapter.markdown import (
    AdapterError,
    parse_document,
    parse_entities,
    serialise_document,
)

_VALID_DOC = """---
document: 300-SPEC-TEST
version: 1
path: projects/p1/requirements/300-SPEC-TEST.md
language: en
status: draft
---

# Title

#### R-300-999

```yaml
id: R-300-999
version: 1
status: draft
category: functional
```

This is the body of the requirement.
"""


@pytest.mark.unit
class TestParseDocument:
    def test_valid_document(self) -> None:
        fm, body = parse_document(_VALID_DOC)
        assert fm.document == "300-SPEC-TEST"
        assert fm.version == 1
        assert fm.language == "en"
        assert "# Title" in body

    def test_missing_fence_raises(self) -> None:
        with pytest.raises(AdapterError, match="frontmatter fence"):
            parse_document("# No frontmatter here")

    def test_unterminated_fence_raises(self) -> None:
        with pytest.raises(AdapterError, match="Unterminated"):
            parse_document("---\ndocument: x\nversion: 1\npath: x\nstatus: draft\n")

    def test_unknown_field_rejected(self) -> None:
        bad = """---
document: 300-SPEC-TEST
version: 1
path: x
status: draft
unknown_field: nope
---

body
"""
        with pytest.raises(AdapterError, match="validation failed"):
            parse_document(bad)


@pytest.mark.unit
class TestParseEntities:
    def test_single_entity(self) -> None:
        _, body = parse_document(_VALID_DOC)
        entities = parse_entities(body)
        assert len(entities) == 1
        assert entities[0].frontmatter.id == "R-300-999"
        assert entities[0].frontmatter.status.value == "draft"
        assert "body of the requirement" in entities[0].body

    def test_multiple_entities(self) -> None:
        doc = _VALID_DOC + """

#### R-300-998

```yaml
id: R-300-998
version: 2
status: approved
category: architecture
```

Second entity body.
"""
        _, body = parse_document(doc)
        entities = parse_entities(body)
        assert len(entities) == 2
        assert {e.frontmatter.id for e in entities} == {"R-300-999", "R-300-998"}

    def test_invalid_entity_id_rejected(self) -> None:
        bad = _VALID_DOC.replace("id: R-300-999", "id: not-an-id")
        _, body = parse_document(bad)
        with pytest.raises(AdapterError, match="validation failed"):
            parse_entities(body)

    def test_unknown_entity_field_rejected(self) -> None:
        bad = _VALID_DOC.replace(
            "category: functional",
            "category: functional\nunknown: banana",
        )
        _, body = parse_document(bad)
        with pytest.raises(AdapterError, match="validation failed"):
            parse_entities(body)


@pytest.mark.unit
class TestSerialise:
    def test_round_trip_preserves_entities(self) -> None:
        fm, body = parse_document(_VALID_DOC)
        reserialised = serialise_document(fm, body)
        fm2, body2 = parse_document(reserialised)
        assert fm2.document == fm.document
        assert fm2.version == fm.version
        entities_a = parse_entities(body)
        entities_b = parse_entities(body2)
        assert len(entities_a) == len(entities_b)
        assert entities_a[0].frontmatter.id == entities_b[0].frontmatter.id
