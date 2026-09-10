"""Whitelist + required-field tests for the 9-entity-type / 11-relation taxonomy."""
import pytest

from kglib.validate import (
    REQUIRED_EDGE_FIELDS,
    REQUIRED_NODE_FIELDS,
    VALID_ENTITY_TYPES,
    VALID_RELATIONS,
    assert_valid,
    validate_extraction,
)


def _node(**over):
    n = {"id": "a", "label": "A", "entity_type": "concept",
         "definition": "", "source_file": "a.md"}
    n.update(over)
    return n


def _edge(**over):
    e = {"source": "a", "target": "b", "relation": "归属",
         "confidence": "EXTRACTED", "source_file": "a.md"}
    e.update(over)
    return e


def test_constant_sets_are_exact():
    assert VALID_ENTITY_TYPES == {
        "concept", "principle", "method", "rule", "procedure",
        "fact", "scenario", "keypoint", "document",
    }
    assert VALID_RELATIONS == {
        "阐述", "归属", "组成", "前置", "解决", "导致",
        "限制", "顺序", "推导", "适用", "影响",
    }
    assert REQUIRED_NODE_FIELDS == {"id", "label", "entity_type", "definition", "source_file"}
    assert REQUIRED_EDGE_FIELDS == {"source", "target", "relation", "confidence", "source_file"}


def test_valid_minimal_extraction():
    data = {"nodes": [_node(), _node(id="b", label="B")], "edges": [_edge()]}
    assert validate_extraction(data) == []


def test_all_nine_entity_types_accepted():
    for t in sorted(VALID_ENTITY_TYPES):
        data = {"nodes": [_node(entity_type=t)], "edges": []}
        assert validate_extraction(data) == [], t


def test_all_eleven_relations_accepted():
    for r in sorted(VALID_RELATIONS):
        data = {"nodes": [_node(), _node(id="b", label="B")],
                "edges": [_edge(relation=r)]}
        assert validate_extraction(data) == [], r


def test_invalid_entity_type_rejected():
    data = {"nodes": [_node(entity_type="paper")], "edges": []}
    errs = validate_extraction(data)
    assert any("invalid entity_type" in e and "'paper'" in e for e in errs)


def test_invalid_relation_rejected():
    data = {"nodes": [_node(), _node(id="b", label="B")],
            "edges": [_edge(relation="references")]}
    errs = validate_extraction(data)
    assert any("invalid relation" in e and "'references'" in e for e in errs)


def test_definition_empty_string_ok_missing_field_errors():
    assert validate_extraction({"nodes": [_node()], "edges": []}) == []
    data = {"nodes": [_node()], "edges": []}
    del data["nodes"][0]["definition"]
    errs = validate_extraction(data)
    assert any("missing required field 'definition'" in e for e in errs)


def test_confidence_enum_unchanged():
    data = {"nodes": [_node(), _node(id="b", label="B")],
            "edges": [_edge(confidence="GUESSED")]}
    errs = validate_extraction(data)
    assert any("invalid confidence" in e for e in errs)


def test_assert_valid_raises_with_all_errors():
    with pytest.raises(ValueError, match="error"):
        assert_valid({"nodes": [_node(entity_type="image")], "edges": []})
