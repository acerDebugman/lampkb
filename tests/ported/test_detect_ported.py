# Ported from graphify/tests/test_detect.py (classification + sensitive-file cases).
# graphify -> kglib; code-only cases (powershell manifests, tree-sitter fixtures)
# dropped. Office (.docx/.xlsx) classification is an intentional docs-only
# difference — pinned by a test below.
from pathlib import Path

from kglib.detect import classify_file, detect, FileType, _is_sensitive


# --- classification (upstream: test_classify_*) -----------------------------

def test_classify_python():
    assert classify_file(Path("foo.py")) == FileType.CODE


def test_classify_markdown():
    assert classify_file(Path("README.md")) == FileType.DOCUMENT


def test_classify_skill():
    # .skill agent files (Markdown with YAML frontmatter) were dropped as unclassified.
    assert classify_file(Path("10_Orchestrator.skill")) == FileType.DOCUMENT


def test_classify_pdf():
    assert classify_file(Path("paper.pdf")) == FileType.PAPER


def test_classify_pdf_in_xcassets_skipped():
    # PDFs inside Xcode asset catalogs are vector icons, not papers
    asset_pdf = Path("MyApp/Images.xcassets/icon.imageset/icon.pdf")
    assert classify_file(asset_pdf) is None


def test_classify_pdf_in_xcassets_root_skipped():
    asset_pdf = Path("Pods/HXPHPicker/Assets.xcassets/photo.pdf")
    assert classify_file(asset_pdf) is None


def test_classify_unknown_returns_none():
    assert classify_file(Path("archive.zip")) is None


def test_classify_image():
    assert classify_file(Path("screenshot.png")) == FileType.IMAGE
    assert classify_file(Path("design.jpg")) == FileType.IMAGE
    assert classify_file(Path("diagram.webp")) == FileType.IMAGE


def test_classify_office_is_unclassified_in_kg():
    # INTENTIONAL DIVERGENCE: upstream classifies .docx/.xlsx as DOCUMENT and
    # converts them to markdown sidecars; kg drops office parsing entirely, so
    # they are unclassified (reported, never parsed).
    assert classify_file(Path("report.docx")) is None
    assert classify_file(Path("data.xlsx")) is None


def test_classify_md_paper_by_signals(tmp_path):
    """A .md file with enough paper signals should classify as PAPER."""
    paper = tmp_path / "paper.md"
    paper.write_text(
        "# Abstract\n\nWe propose a new method. See [1] and [23].\n"
        "This work was published in the Journal of AI. ArXiv preprint.\n"
        "See Equation 3 for details. \\cite{vaswani2017}.\n"
    )
    assert classify_file(paper) == FileType.PAPER


def test_classify_md_doc_without_signals(tmp_path):
    """A plain .md file without paper signals should stay DOCUMENT."""
    doc = tmp_path / "notes.md"
    doc.write_text("# My Notes\n\nHere are some notes about the project.\n")
    assert classify_file(doc) == FileType.DOCUMENT


# --- sensitive-file detection (upstream regression set) ---

def test_sensitive_flags_api_token_txt():
    assert _is_sensitive(Path("api_token.txt"))


def test_sensitive_flags_oauth_token_json():
    assert _is_sensitive(Path("oauth_token.json"))


def test_sensitive_flags_underscore_secret():
    assert _is_sensitive(Path("app_secret.yaml"))


def test_sensitive_does_not_flag_tokenizer_py():
    assert not _is_sensitive(Path("tokenizer.py"))


def test_sensitive_does_not_flag_passwords_py():
    # A programming-language source file named after a domain noun is a
    # module, not a secret store.
    assert not _is_sensitive(Path("passwords.py"))


def test_sensitive_does_not_flag_ruby_code_modules():
    # Exact cases: Rails source modules with keyword-ish names must survive.
    assert not _is_sensitive(Path("app/models/device_token.rb"))
    assert not _is_sensitive(Path("app/controllers/api/v1/passwords_controller.rb"))


def test_sensitive_still_flags_data_secret_stores():
    # Guard: the exemption is ONLY for real source code, not data/config
    # formats — credentials.json / oauth_token.json / secrets.yaml are the secret
    # stores Stage 3 must keep catching (even though .json routes through CODE).
    assert _is_sensitive(Path("credentials.json"))
    assert _is_sensitive(Path("oauth_token.json"))


def test_sensitive_prose_topic_slugs_survive(tmp_path):
    # A heavily-linked wiki article ABOUT tokens is not a credential
    # store; a bare keyword name (secrets.md) still reads as a dump.
    assert not _is_sensitive(Path("token-economics-of-recall.md"))
    assert _is_sensitive(Path("secrets.md"))


# --- detect() integration (upstream: test_detect_skips_noise_dot_dirs, adapted
# --- to tmp_path; and a sensitive-file end-to-end skip) ----------------------

def test_detect_skips_noise_dirs(tmp_path):
    """Noise dirs (framework caches, venvs, kg-out itself) are skipped;
    ALL hidden dot dirs (.github, .codewhale, ...) are pruned too."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "real.md").write_text("# Real doc\n")
    (tmp_path / ".next").mkdir()
    (tmp_path / ".next" / "cached.md").write_text("# cached\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.md").write_text("# dep\n")
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "workflow.md").write_text("# workflow notes\n")
    (tmp_path / ".codewhale").mkdir()
    (tmp_path / ".codewhale" / "instructions.md").write_text("# instructions\n")
    result = detect(tmp_path)
    all_files = [f for files in result["files"].values() for f in files]
    assert any(f.endswith("docs/real.md") for f in all_files)
    assert not any("/.next/" in f for f in all_files)
    assert not any("/node_modules/" in f for f in all_files)
    assert not any("/.github/" in f for f in all_files)
    assert not any("/.codewhale/" in f for f in all_files)


def test_detect_skips_sensitive_files(tmp_path):
    (tmp_path / "notes.md").write_text("# Notes\n")
    (tmp_path / "api_token.txt").write_text("tok_123")
    result = detect(tmp_path)
    docs = result["files"]["document"]
    assert any(f.endswith("notes.md") for f in docs)
    assert not any("api_token" in f for f in docs)
    assert any("api_token.txt" in f for f in result["skipped_sensitive"])


# --- second-pass additions (sweep): word counting, corpus warning, ignore files ---

def test_count_words_markdown(tmp_path):
    """Adapted from test_count_words_sample_md (upstream used a fixture file)."""
    from kglib.detect import count_words
    f = tmp_path / "sample.md"
    f.write_text("# Title\n\nSome body text with several words in it.\n")
    assert count_words(f) > 5


def test_detect_warns_small_corpus(tmp_path):
    """Adapted from test_detect_warns_small_corpus (fixture-based upstream)."""
    (tmp_path / "note.md").write_text("# Small\n\nJust a few words.\n")
    result = detect(tmp_path)
    assert result["warning"] is not None
    assert "fits in a single context window" in result["warning"]
    assert result["needs_graph"] is False


def test_kgignore_excludes_file(tmp_path):
    """Files matching .kgignore patterns are excluded from detect()."""
    (tmp_path / ".kgignore").write_text("vendor/\n*.generated.md\n")
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    (vendor / "lib.md").write_text("# vendored")
    (tmp_path / "main.md").write_text("# main")
    (tmp_path / "schema.generated.md").write_text("# generated")

    result = detect(tmp_path)
    file_list = result["files"]["document"]
    assert any("main.md" in f for f in file_list)
    assert not any("vendor" in f for f in file_list)
    assert not any("generated" in f for f in file_list)
    assert result["kgignore_patterns"] == 2


def test_kgignore_matches_nfd_path_with_nfc_pattern(tmp_path):
    """An accented pattern excludes its directory even when the FS stores NFD.

    macOS returns filenames in NFD ("c" + U+0327) while editors write ignore
    files in NFC (U+00E7). Without normalization the two compare unequal and
    the rule silently does nothing — the files get scanned, and docs/PDFs are
    sent to an LLM despite an explicit exclusion.
    """
    import unicodedata
    nfc_name = unicodedata.normalize("NFC", "Orçamento")
    nfd_name = unicodedata.normalize("NFD", nfc_name)
    assert nfc_name != nfd_name  # guard: the two forms really do differ

    (tmp_path / ".kgignore").write_text(f"{nfc_name}/\n", encoding="utf-8")
    secret_dir = tmp_path / nfd_name
    secret_dir.mkdir()
    (secret_dir / "contrato.md").write_text("# contrato")
    (tmp_path / "main.md").write_text("# main")

    result = detect(tmp_path)
    file_list = result["files"]["document"]
    assert any("main.md" in f for f in file_list)
    assert not any("contrato.md" in f for f in file_list)


def test_kgignore_matches_nfc_path_with_nfd_pattern(tmp_path):
    """The reverse direction also holds: NFD pattern, NFC path on disk."""
    import unicodedata
    nfc_name = unicodedata.normalize("NFC", "Orçamento")
    nfd_name = unicodedata.normalize("NFD", nfc_name)

    (tmp_path / ".kgignore").write_text(f"{nfd_name}/\n", encoding="utf-8")
    d = tmp_path / nfc_name
    d.mkdir()
    (d / "contrato.md").write_text("# contrato")
    (tmp_path / "main.md").write_text("# main")

    result = detect(tmp_path)
    file_list = result["files"]["document"]
    assert any("main.md" in f for f in file_list)
    assert not any("contrato.md" in f for f in file_list)


def test_kgignore_ascii_patterns_unaffected(tmp_path):
    """Normalization is a no-op for ASCII patterns — no regression."""
    (tmp_path / ".kgignore").write_text("vendor/\n")
    v = tmp_path / "vendor"
    v.mkdir()
    (v / "lib.md").write_text("# vendored")
    (tmp_path / "main.md").write_text("# main")

    result = detect(tmp_path)
    file_list = result["files"]["document"]
    assert any("main.md" in f for f in file_list)
    assert not any("vendor" in f for f in file_list)


def test_kgignore_missing_is_fine(tmp_path):
    """No .kgignore is not an error."""
    (tmp_path / "main.md").write_text("# main")
    result = detect(tmp_path)
    assert result["kgignore_patterns"] == 0


def test_kgignore_comments_ignored(tmp_path):
    """Comment lines in .kgignore are not treated as patterns."""
    (tmp_path / ".kgignore").write_text("# this is a comment\n\nmain.md\n")
    (tmp_path / "main.md").write_text("# main")
    (tmp_path / "other.md").write_text("# other")
    result = detect(tmp_path)
    assert not any("main.md" in f for f in result["files"]["document"])
    assert any("other.md" in f for f in result["files"]["document"])
