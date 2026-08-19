"""Shared pytest fixtures for the taoskg test suite.

Makes ``kglib`` (and the ``kg`` entry module) importable by pointing sys.path
at the vendored scripts directory, and provides tmp-corpus fixtures.
"""
from __future__ import annotations

import json
import sys
from argparse import Namespace
from pathlib import Path

import pytest

TAOSKG_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = TAOSKG_ROOT / "skills" / "kg" / "scripts"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# Upstream graphify checkout (used by Layer 1 equivalence tests only).
UPSTREAM_DIR = TAOSKG_ROOT.parent / "graphify"


@pytest.fixture
def scripts_dir() -> Path:
    return SCRIPTS_DIR


@pytest.fixture
def ns():
    """Build an argparse.Namespace for calling kg.py cmd_* functions directly."""
    def _make(**kwargs):
        return Namespace(**kwargs)
    return _make


# --- tmp corpus ------------------------------------------------------------

_CORPUS_FILES = {
    "alpha.md": (
        "# Alpha Service\n\n"
        "Alpha is the entry point service. It handles authentication requests and\n"
        "delegates session management to the Beta component. See the Beta notes for\n"
        "the token refresh flow. Alpha depends on the shared Config Loader.\n"
    ),
    "beta.md": (
        "# Beta Component\n\n"
        "Beta manages sessions. It issues tokens and refreshes them on expiry.\n"
        "Beta reads its timeouts from the Config Loader and writes audit events\n"
        "to the Gamma audit log.\n"
    ),
    "docs/gamma.md": (
        "# Gamma Audit Log\n\n"
        "Gamma is the audit log. Every authentication decision made by Alpha is\n"
        "recorded here. Gamma batches writes for throughput.\n"
    ),
    "docs/config.md": (
        "# Config Loader\n\n"
        "The Config Loader reads YAML configuration and provides typed settings\n"
        "to Alpha and Beta. It watches the config file for changes and reloads.\n"
    ),
    "docs/delta.md": (
        "# Delta Metrics\n\n"
        "Delta collects metrics from Alpha, Beta and Gamma. It exposes a Prometheus\n"
        "endpoint. Delta uses the Config Loader for scrape intervals.\n"
    ),
}


@pytest.fixture
def docs_corpus(tmp_path) -> Path:
    """A 5-file markdown corpus with cross-references, in a tmp dir."""
    corpus = tmp_path / "corpus"
    for rel, text in _CORPUS_FILES.items():
        p = corpus / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return corpus


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
