"""Standalone writer for the smoke-test extraction chunks.

Extracted from ``tests/conftest.py`` so the hand-written extraction payloads
can be produced outside pytest, e.g.::

    python tests/write_smoke_chunks.py ./corpus

Writes ``<corpus>/kg-out/.kg_chunk_01.json`` and ``.kg_chunk_02.json``
mimicking what the host agent (the LLM extractor) would write.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _node(nid, label, file_type, source_file, **extra):
    n = {
        "id": nid, "label": label, "file_type": file_type,
        "source_file": source_file, "source_location": None,
        "source_url": None, "captured_at": None, "author": None, "contributor": None,
    }
    n.update(extra)
    return n


def _edge(source, target, relation, confidence, score, source_file):
    return {
        "source": source, "target": target, "relation": relation,
        "confidence": confidence, "confidence_score": score,
        "source_file": source_file, "source_location": None, "weight": 1.0,
    }


def write_smoke_chunks(corpus: Path) -> int:
    """Hand-written extraction chunks covering the docs_corpus fixture.

    Mimics what the host agent (the LLM extractor) would write to
    kg-out/.kg_chunk_NN.json. Returns the number of chunk files written.
    """
    a = str(corpus / "alpha.md")
    b = str(corpus / "beta.md")
    g = str(corpus / "docs" / "gamma.md")
    c = str(corpus / "docs" / "config.md")
    d = str(corpus / "docs" / "delta.md")

    chunk1 = {
        "nodes": [
            _node("alpha_alpha_service", "Alpha Service", "document", a),
            _node("alpha_authentication", "Authentication", "concept", a),
            _node("beta_beta_component", "Beta Component", "document", b),
            _node("beta_session_management", "Session Management", "concept", b),
            _node("beta_token_refresh", "Token Refresh", "concept", b),
        ],
        "edges": [
            _edge("alpha_alpha_service", "alpha_authentication", "references", "EXTRACTED", 1.0, a),
            _edge("alpha_alpha_service", "beta_beta_component", "references", "EXTRACTED", 1.0, a),
            _edge("beta_beta_component", "beta_session_management", "references", "EXTRACTED", 1.0, b),
            _edge("beta_session_management", "beta_token_refresh", "conceptually_related_to", "INFERRED", 0.85, b),
        ],
        "hyperedges": [],
        "input_tokens": 0, "output_tokens": 0,
    }
    chunk2 = {
        "nodes": [
            _node("docs_gamma_gamma_audit_log", "Gamma Audit Log", "document", g),
            _node("docs_config_config_loader", "Config Loader", "document", c),
            _node("docs_delta_delta_metrics", "Delta Metrics", "document", d),
        ],
        "edges": [
            _edge("docs_gamma_gamma_audit_log", "alpha_authentication", "references", "EXTRACTED", 1.0, g),
            _edge("alpha_alpha_service", "docs_config_config_loader", "references", "EXTRACTED", 1.0, c),
            _edge("beta_beta_component", "docs_config_config_loader", "references", "EXTRACTED", 1.0, c),
            _edge("docs_delta_delta_metrics", "docs_config_config_loader", "references", "EXTRACTED", 1.0, d),
            _edge("docs_delta_delta_metrics", "docs_gamma_gamma_audit_log", "semantically_similar_to", "INFERRED", 0.65, d),
        ],
        "hyperedges": [
            {"id": "shared_config_dependency", "label": "Shared Config Dependency",
             "nodes": ["alpha_alpha_service", "beta_beta_component", "docs_config_config_loader"],
             "relation": "participate_in", "confidence": "INFERRED",
             "confidence_score": 0.75, "source_file": c},
        ],
        "input_tokens": 0, "output_tokens": 0,
    }

    out = corpus / "kg-out"
    out.mkdir(parents=True, exist_ok=True)
    for i, chunk in enumerate((chunk1, chunk2), 1):
        (out / f".kg_chunk_{i:02d}.json").write_text(
            json.dumps(chunk, ensure_ascii=False), encoding="utf-8")
    return 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Write the smoke-test extraction chunks into <corpus>/kg-out.")
    parser.add_argument("corpus", type=Path, help="corpus root directory")
    args = parser.parse_args()
    n = write_smoke_chunks(args.corpus.resolve())
    print(f"chunks written: {n}")
