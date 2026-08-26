"""Shared pytest fixtures for the taoskg test suite.

Makes ``kglib`` (and the ``kg`` entry module) importable by pointing sys.path
at the vendored scripts directory, and provides tmp-corpus fixtures.
"""
from __future__ import annotations

import sys
from argparse import Namespace
from pathlib import Path

import pytest

# Re-exported for test modules that import it from conftest (test_smoke.py:16).
from write_smoke_chunks import _edge, _node, write_smoke_chunks  # noqa: F401

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

