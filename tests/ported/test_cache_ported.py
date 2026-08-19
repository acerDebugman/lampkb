# Ported from graphify/tests/test_cache.py (hash + semantic-cache cases).
# graphify -> kglib. AST-cache versioning tests dropped (kg has no AST cache).
import pytest
from pathlib import Path

from kglib.cache import file_hash, load_cached, save_cached, save_semantic_cache


@pytest.fixture
def tmp_file(tmp_path):
    f = tmp_path / "sample.txt"
    f.write_text("hello world")
    return f


@pytest.fixture
def cache_root(tmp_path):
    return tmp_path


def test_file_hash_consistent(tmp_file):
    """Same file gives same hash on repeated calls."""
    h1 = file_hash(tmp_file)
    h2 = file_hash(tmp_file)
    assert h1 == h2
    assert isinstance(h1, str)
    assert len(h1) == 64  # SHA256 hex digest length


def test_file_hash_changes(tmp_path):
    """Different file contents give different hashes."""
    f1 = tmp_path / "a.txt"
    f2 = tmp_path / "b.txt"
    f1.write_text("content one")
    f2.write_text("content two")
    assert file_hash(f1) != file_hash(f2)


def test_cache_roundtrip(tmp_file, cache_root):
    """Save then load returns the same result dict."""
    result = {"nodes": [{"id": "n1", "label": "Node1"}], "edges": []}
    save_cached(tmp_file, result, root=cache_root)
    loaded = load_cached(tmp_file, root=cache_root)
    assert loaded == result


def test_cache_miss_on_change(tmp_file, cache_root):
    """After file content changes, load_cached returns None."""
    result = {"nodes": [], "edges": [{"source": "a", "target": "b"}]}
    save_cached(tmp_file, result, root=cache_root)
    # Modify the file
    tmp_file.write_text("completely different content")
    assert load_cached(tmp_file, root=cache_root) is None


def test_md_frontmatter_only_change_same_hash(tmp_path):
    """Changing only frontmatter fields in a .md file does not change the hash."""
    f = tmp_path / "doc.md"
    f.write_text("---\nreviewed: 2026-01-01\n---\n\n# Title\n\nBody text.")
    h1 = file_hash(f)
    f.write_text("---\nreviewed: 2026-04-09\n---\n\n# Title\n\nBody text.")
    h2 = file_hash(f)
    assert h1 == h2


def test_md_body_change_different_hash(tmp_path):
    """Changing the body of a .md file produces a different hash."""
    f = tmp_path / "doc.md"
    f.write_text("---\nreviewed: 2026-01-01\n---\n\n# Title\n\nOriginal body.")
    h1 = file_hash(f)
    f.write_text("---\nreviewed: 2026-01-01\n---\n\n# Title\n\nChanged body.")
    h2 = file_hash(f)
    assert h1 != h2


def test_save_semantic_cache_overwrites_by_default(tmp_path):
    """Default save_semantic_cache replaces a file's cached entry (the final,
    authoritative write in the extract pipeline)."""
    f = tmp_path / "doc.md"; f.write_text("# Doc\n")
    save_semantic_cache([{"id": "a", "source_file": "doc.md"}], [], root=tmp_path)
    save_semantic_cache([{"id": "b", "source_file": "doc.md"}], [], root=tmp_path)
    cached = load_cached(f, root=tmp_path, kind="semantic")
    ids = {n["id"] for n in cached["nodes"]}
    assert ids == {"b"}, "default must overwrite, not accumulate"


def test_save_semantic_cache_rejects_out_of_scope_source_file(tmp_path):
    """#1757: an undispatched file must keep its complete cache entry when a
    semantic result misattributes a node to it."""
    intended = tmp_path / "intended.md"
    intended.write_text("# Intended\n")
    protected = tmp_path / "protected.md"
    protected.write_text("# Protected\n")

    save_semantic_cache(
        [{"id": "original", "source_file": "protected.md"}],
        [],
        root=tmp_path,
    )

    nodes = [
        {"id": "expected", "source_file": str(intended.resolve())},
        {"id": "stray", "source_file": "protected.md"},
    ]
    edges = [
        {"source": "stray", "target": "expected", "source_file": "protected.md"},
    ]
    hyperedges = [
        {"id": "stray_hyperedge", "nodes": ["stray"], "source_file": "protected.md"},
    ]

    with pytest.warns(RuntimeWarning, match="out-of-scope source_file 'protected.md'"):
        saved = save_semantic_cache(
            nodes,
            edges,
            hyperedges,
            root=tmp_path,
            allowed_source_files=["intended.md"],
        )

    assert saved == 1
    intended_cache = load_cached(intended, root=tmp_path, kind="semantic")
    assert {node["id"] for node in intended_cache["nodes"]} == {"expected"}

    protected_cache = load_cached(protected, root=tmp_path, kind="semantic")
    assert {node["id"] for node in protected_cache["nodes"]} == {"original"}
    assert protected_cache["edges"] == []
    assert protected_cache["hyperedges"] == []


def test_semantic_cache_check_returns_uncached(tmp_path):
    """check_semantic_cache splits a file list into hits and misses."""
    from kglib.cache import check_semantic_cache
    f = tmp_path / "doc.md"
    f.write_text("# Doc\n")
    save_semantic_cache([{"id": "a", "source_file": "doc.md"}], [], root=tmp_path)
    cached_nodes, cached_edges, cached_hyperedges, uncached = check_semantic_cache(
        [str(f)], root=tmp_path)
    assert uncached == []
    assert {n["id"] for n in cached_nodes} == {"a"}
    f.write_text("# Doc v2 - changed body\n")
    _n, _e, _h, uncached = check_semantic_cache([str(f)], root=tmp_path)
    assert uncached == [str(f)]
