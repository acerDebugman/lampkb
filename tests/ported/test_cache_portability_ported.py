# Ported from graphify/tests/test_word_count_cache.py (in full — word-count
# caching is docs-relevant: detect() counts words in every PDF/markdown file) and
# the source_file portability section of graphify/tests/test_cache.py.
# graphify -> kglib.
#
# kind="ast" note: kg drops the AST cache's per-version namespace, but
# save_cached/load_cached/cache_dir still accept kind="ast" as a plain
# directory, so the portability mechanics below are exercised unchanged.
import hashlib
import json
import os
import shutil
from pathlib import Path

from kglib import cache


def _settle(path: Path) -> None:
    """Backdate mtime past the racily-clean window so the stat fastpath is
    allowed to serve this file.

    A just-written file is deliberately never trusted: its mtime tick may still
    be open, so a same-length rewrite could hide behind an identical
    (size, mtime_ns). See cache._stat_sig_fresh. Any test asserting a warm stat
    hit therefore has to settle the file first.
    """
    old = path.stat().st_mtime_ns - 10 * 1_000_000_000
    os.utime(path, ns=(old, old))


# --- word-count cache ---------------------------------------------------------

def test_word_count_cached_until_file_changes(tmp_path, monkeypatch):
    # Isolate the stat index to this tmp root.
    monkeypatch.setattr(cache, "_stat_index", {})
    monkeypatch.setattr(cache, "_stat_index_root", None)

    f = tmp_path / "doc.txt"
    f.write_text("one two three four five")
    _settle(f)

    calls = {"n": 0}
    def compute(p: Path) -> int:
        calls["n"] += 1
        return len(p.read_text().split())

    assert cache.cached_word_count(f, tmp_path, compute) == 5
    assert calls["n"] == 1
    # Second call, file unchanged → served from cache, compute NOT re-run.
    assert cache.cached_word_count(f, tmp_path, compute) == 5
    assert calls["n"] == 1

    # Change the file → recompute.
    f.write_text("only three words now")  # 4 words
    assert cache.cached_word_count(f, tmp_path, compute) == 4
    assert calls["n"] == 2


def test_word_count_augments_existing_hash_entry(tmp_path, monkeypatch):
    # cached_word_count must not clobber a hash already stored for the file.
    monkeypatch.setattr(cache, "_stat_index", {})
    monkeypatch.setattr(cache, "_stat_index_root", None)

    f = tmp_path / "m.md"
    f.write_text("# a b\n")  # -> ["#", "a", "b"] == 3 tokens
    h = cache.file_hash(f, tmp_path)
    assert h
    wc = cache.cached_word_count(f, tmp_path, lambda p: len(p.read_text().split()))
    assert wc == 3
    # The hash entry survives alongside the word_count.
    assert cache.file_hash(f, tmp_path) == h
    key = str(cache._normalize_path(f).resolve())
    entry = cache._stat_index[key]
    # Digests are now stored per salt under "hashes" (salt = path relative
    # to root == "m.md" here), co-located with the word_count.
    assert entry.get("hashes", {}).get("m.md") == h and entry.get("word_count") == 3


def test_file_hash_is_order_independent_across_roots(tmp_path, monkeypatch):
    """The stat-index memo must be keyed by the salt (path relative to
    root) that enters the digest, so the same (file, root) returns the same
    digest regardless of what root was hashed first."""
    monkeypatch.setattr(cache, "_stat_index", {})
    monkeypatch.setattr(cache, "_stat_index_root", None)

    root_a = tmp_path / "a"; root_a.mkdir()
    f = root_a / "doc.txt"; f.write_text("hello world\n")
    root_b = tmp_path / "b"; root_b.mkdir()  # f is NOT under root_b -> abs-path salt

    content = f.read_bytes()
    exp_rel = hashlib.sha256(content + b"\x00" + b"doc.txt").hexdigest()
    exp_abs = hashlib.sha256(
        content + b"\x00" + str(cache._normalize_path(f).resolve()).replace("\\", "/").lower().encode()
    ).hexdigest()

    # rel-first order
    assert cache.file_hash(f, root_a) == exp_rel
    assert cache.file_hash(f, root_b) == exp_abs      # not served the rel digest
    assert cache.file_hash(f, root_a) == exp_rel      # still stable

    # abs-first order, fresh index
    monkeypatch.setattr(cache, "_stat_index", {})
    monkeypatch.setattr(cache, "_stat_index_root", None)
    assert cache.file_hash(f, root_b) == exp_abs
    assert cache.file_hash(f, root_a) == exp_rel      # not served the abs digest


def test_file_hash_ignores_legacy_unsalted_entry(tmp_path, monkeypatch):
    """A legacy entry carrying a bare "hash" (no salt) is never trusted."""
    monkeypatch.setattr(cache, "_stat_index", {})
    monkeypatch.setattr(cache, "_stat_index_root", None)
    f = tmp_path / "m.md"; f.write_text("# a b\n")
    st = f.stat()
    key = str(cache._normalize_path(f).resolve())
    cache._stat_index[key] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "hash": "deadbeef"}
    exp = hashlib.sha256(f.read_bytes() + b"\x00" + b"m.md").hexdigest()
    assert cache.file_hash(f, tmp_path) == exp        # recomputed, not "deadbeef"
    entry = cache._stat_index[key]
    assert "hash" not in entry and entry["hashes"]["m.md"] == exp


# --- source_file portability --------------------------------------------------

def test_save_cached_relativizes_source_file(tmp_path):
    """The on-disk cache JSON contains forward-slash relative source_file
    entries — no absolute prefix from the saving machine leaks in."""
    (tmp_path / "docs").mkdir()
    src = tmp_path / "docs" / "foo.md"
    src.write_text("# foo\n")
    abs_src = str(src.resolve())
    result = {
        "nodes": [{"id": "n1", "label": "foo", "source_file": abs_src}],
        "edges": [{"source": "n1", "target": "n1", "source_file": abs_src}],
    }
    cache.save_cached(src, result, root=tmp_path, kind="semantic")

    h = cache.file_hash(src, tmp_path)
    entry = cache.cache_dir(tmp_path, "semantic") / f"{h}.json"
    on_disk = json.loads(entry.read_text(encoding="utf-8"))
    node_sources = {n["source_file"] for n in on_disk["nodes"]}
    edge_sources = {e["source_file"] for e in on_disk["edges"]}
    assert node_sources == {"docs/foo.md"}, (
        f"cache nodes must store relative source_file; got {node_sources}"
    )
    assert edge_sources == {"docs/foo.md"}


def test_load_cached_absolutizes_source_file(tmp_path):
    """``load_cached`` returns the same absolute-path shape that a fresh
    extraction produces, so consumers don't need to special-case cache
    hits vs. fresh extraction."""
    (tmp_path / "docs").mkdir()
    src = tmp_path / "docs" / "foo.md"
    src.write_text("# foo\n")
    abs_src = str(src.resolve())
    cache.save_cached(src, {
        "nodes": [{"id": "n1", "source_file": abs_src}],
        "edges": [{"source": "n1", "target": "n1", "source_file": abs_src}],
    }, root=tmp_path, kind="semantic")

    loaded = cache.load_cached(src, root=tmp_path, kind="semantic")
    assert loaded is not None
    assert loaded["nodes"][0]["source_file"] == abs_src
    assert loaded["edges"][0]["source_file"] == abs_src


def test_load_cached_passes_through_legacy_absolute_source_file(tmp_path):
    """Cache entries written with an absolute source_file inside must still load
    correctly: the absolutize step is a no-op for already-absolute values."""
    (tmp_path / "docs").mkdir()
    src = tmp_path / "docs" / "foo.md"
    src.write_text("# foo\n")
    abs_src = str(src.resolve())

    # Hand-write a legacy-format cache entry (absolute source_file).
    h = cache.file_hash(src, tmp_path)
    entry = cache.cache_dir(tmp_path, "semantic") / f"{h}.json"
    entry.write_text(json.dumps({
        "nodes": [{"id": "n1", "source_file": abs_src}],
        "edges": [],
    }))

    loaded = cache.load_cached(src, root=tmp_path, kind="semantic")
    assert loaded is not None
    assert loaded["nodes"][0]["source_file"] == abs_src


def test_cache_portable_across_roots(tmp_path):
    """End-to-end portability: a cache entry written at one root can be
    consumed at a different absolute root because the file is content-hashed
    AND its embedded source_file is stored relative."""
    repo_a = tmp_path / "repo_a"
    repo_a.mkdir()
    (repo_a / "docs").mkdir()
    src_a = repo_a / "docs" / "foo.md"
    src_a.write_text("# foo\n")
    cache.save_cached(src_a, {
        "nodes": [{"id": "n1", "source_file": str(src_a.resolve())}],
        "edges": [],
    }, root=repo_a, kind="semantic")

    # Copy corpus + cache to a second location with a different absolute prefix.
    repo_b = tmp_path / "repo_b"
    shutil.copytree(repo_a, repo_b)

    src_b = repo_b / "docs" / "foo.md"
    loaded = cache.load_cached(src_b, root=repo_b, kind="semantic")
    assert loaded is not None, (
        "cache must port across absolute prefixes (content hash + relative source_file)"
    )
    # Source path re-anchored to the new root, not the old one.
    assert loaded["nodes"][0]["source_file"] == str(src_b.resolve())
    assert not str(repo_a) in loaded["nodes"][0]["source_file"]


# --- deep-mode semantic cache namespace ---------------------------------------

def test_semantic_cache_deep_mode_roundtrip_under_deep_namespace(tmp_path):
    """mode='deep' saves under cache/semantic-deep/ and reads back from it."""
    f = tmp_path / "doc.md"
    f.write_text("# Doc\n")
    cache.save_semantic_cache([{"id": "a", "source_file": "doc.md"}], [], root=tmp_path, mode="deep")
    assert (tmp_path / "kg-out" / "cache" / "semantic-deep").is_dir()
    cached = cache.load_cached(f, root=tmp_path, kind="semantic-deep")
    assert {n["id"] for n in cached["nodes"]} == {"a"}


def test_semantic_cache_deep_invisible_to_plain_reads_and_vice_versa(tmp_path):
    """Deep-mode results never shadow standard-mode entries for the same
    content (and vice versa)."""
    f = tmp_path / "doc.md"
    f.write_text("# Doc\n")
    cache.save_semantic_cache([{"id": "plain", "source_file": "doc.md"}], [], root=tmp_path)
    cache.save_semantic_cache([{"id": "deep", "source_file": "doc.md"}], [], root=tmp_path, mode="deep")
    plain = cache.load_cached(f, root=tmp_path, kind="semantic")
    deep = cache.load_cached(f, root=tmp_path, kind="semantic-deep")
    assert {n["id"] for n in plain["nodes"]} == {"plain"}
    assert {n["id"] for n in deep["nodes"]} == {"deep"}
