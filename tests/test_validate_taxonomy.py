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


def test_dedup_file_anchored_set_contains_only_document():
    from kglib.dedup import _FILE_ANCHORED_NONCODE
    assert _FILE_ANCHORED_NONCODE == frozenset({"document"})


def test_analyze_has_no_legacy_relation_special_cases():
    import kglib.analyze as analyze
    assert not hasattr(analyze, "find_import_cycles")
    assert not hasattr(analyze, "_cross_language")


def test_dedup_has_no_code_branch():
    import kglib.dedup as dedup
    assert not hasattr(dedup, "_is_code")


def test_to_html_renders_entity_type_and_definition(tmp_path):
    import networkx as nx
    from kglib.export import to_html
    G = nx.Graph()
    G.add_node("n1", label="元素", entity_type="concept",
               definition="元素是 IDMP 中描述资产的基本单元。",
               source_file="a.md", community=0)
    G.add_node("n2", label="规则X", entity_type="rule", definition="",
               source_file="a.md", community=0)
    out = tmp_path / "graph.html"
    to_html(G, {0: ["n1", "n2"]}, str(out))
    html = out.read_text(encoding="utf-8")
    assert "元素是 IDMP 中描述资产的基本单元。" in html
    assert "_entity_type" in html
    assert "_file_type" not in html


def test_extraction_docs_fixture_covers_full_taxonomy():
    import json
    from pathlib import Path
    fixture = json.loads((Path(__file__).parent / "fixtures" / "extraction_docs.json")
                         .read_text(encoding="utf-8"))
    assert validate_extraction(fixture) == []
    assert "hyperedges" not in fixture
    assert {n["entity_type"] for n in fixture["nodes"]} == VALID_ENTITY_TYPES
    assert {e["relation"] for e in fixture["edges"]} == VALID_RELATIONS
    assert all("definition" in n for n in fixture["nodes"])
    assert any(n["definition"] == "" for n in fixture["nodes"])
    assert any(n["definition"] for n in fixture["nodes"])
    assert len(fixture["nodes"]) == 18
    assert len(fixture["edges"]) == 18
