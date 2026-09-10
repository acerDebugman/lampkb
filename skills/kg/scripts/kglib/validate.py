# validate extraction JSON against the kg taxonomy schema before graph assembly
from __future__ import annotations

VALID_ENTITY_TYPES = {
    "concept", "principle", "method", "rule", "procedure",
    "fact", "scenario", "keypoint", "document",
}
VALID_RELATIONS = {
    "阐述", "归属", "组成", "前置", "解决", "导致",
    "限制", "顺序", "推导", "适用", "影响",
}
VALID_CONFIDENCES = {"EXTRACTED", "INFERRED", "AMBIGUOUS"}
REQUIRED_NODE_FIELDS = {"id", "label", "entity_type", "definition", "source_file"}
REQUIRED_EDGE_FIELDS = {"source", "target", "relation", "confidence", "source_file"}


def validate_extraction(data: dict) -> list[str]:
    """
    Validate an extraction JSON dict against the kg taxonomy schema.
    Returns a list of error strings - empty list means valid.
    """
    if not isinstance(data, dict):
        return ["Extraction must be a JSON object"]

    errors: list[str] = []

    # Collected during the node pass so the edge pass can reuse it. Only
    # hashable ids land here; a non-hashable id (e.g. a list emitted by a
    # malformed LLM extraction) is reported as an error rather than crashing
    # the validator on set construction.
    node_ids: set = set()

    # Nodes
    if "nodes" not in data:
        errors.append("Missing required key 'nodes'")
    elif not isinstance(data["nodes"], list):
        errors.append("'nodes' must be a list")
    else:
        for i, node in enumerate(data["nodes"]):
            if not isinstance(node, dict):
                errors.append(f"Node {i} must be an object")
                continue
            for field in REQUIRED_NODE_FIELDS:
                if field not in node:
                    errors.append(f"Node {i} (id={node.get('id', '?')!r}) missing required field '{field}'")
            if "id" in node:
                try:
                    hash(node["id"])
                except TypeError:
                    errors.append(
                        f"Node {i} has non-hashable id {node['id']!r} - id must be a string"
                    )
                else:
                    node_ids.add(node["id"])
            if "entity_type" in node and node["entity_type"] not in VALID_ENTITY_TYPES:
                errors.append(
                    f"Node {i} (id={node.get('id', '?')!r}) has invalid entity_type "
                    f"'{node['entity_type']}' - must be one of {sorted(VALID_ENTITY_TYPES)}"
                )

    # Edges - accept "links" (NetworkX <= 3.1) as fallback for "edges"
    edge_list = data.get("edges") if "edges" in data else data.get("links")
    if edge_list is None:
        errors.append("Missing required key 'edges'")
    elif not isinstance(edge_list, list):
        errors.append("'edges' must be a list")
    else:
        for i, edge in enumerate(edge_list):
            if not isinstance(edge, dict):
                errors.append(f"Edge {i} must be an object")
                continue
            for field in REQUIRED_EDGE_FIELDS:
                if field not in edge:
                    errors.append(f"Edge {i} missing required field '{field}'")
            if "relation" in edge and edge["relation"] not in VALID_RELATIONS:
                errors.append(
                    f"Edge {i} has invalid relation '{edge['relation']}' "
                    f"- must be one of {sorted(VALID_RELATIONS)}"
                )
            if "confidence" in edge and edge["confidence"] not in VALID_CONFIDENCES:
                errors.append(
                    f"Edge {i} has invalid confidence '{edge['confidence']}' "
                    f"- must be one of {sorted(VALID_CONFIDENCES)}"
                )
            for endpoint in ("source", "target"):
                if endpoint not in edge:
                    continue
                val = edge[endpoint]
                try:
                    unmatched = bool(node_ids) and val not in node_ids
                except TypeError:
                    errors.append(
                        f"Edge {i} {endpoint} {val!r} is non-hashable - must be a string"
                    )
                    continue
                if unmatched:
                    errors.append(f"Edge {i} {endpoint} '{val}' does not match any node id")

    return errors


def assert_valid(data: dict) -> None:
    """Raise ValueError with all errors if extraction is invalid."""
    errors = validate_extraction(data)
    if errors:
        msg = f"Extraction JSON has {len(errors)} error(s):\n" + "\n".join(f"  • {e}" for e in errors)
        raise ValueError(msg)
