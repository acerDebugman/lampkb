# Vendored from graphify (https://github.com/safishamsi/graphify), slimmed to documents-only.
"""Single source of truth for the kg output-directory name.

The output directory is the literal ``kg-out`` under the current working
directory. Upstream graphify made this overridable with an env var
(worktrees or shared-output setups); kg hardcodes ``kg-out`` and
deletes the env-var mechanism entirely.

This used to be duplicated as an identical constant in
``__main__``, ``cache``, and ``watch``, while ``security`` and ``callflow_html``
hardcoded the literal output-dir name and silently ignored the override.
Centralising it here keeps the name in one place.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath

KG_OUT = "kg-out"


def _atomic_replace(path: "str | Path", write_fn) -> None:
    """Atomically replace ``path`` with content written by ``write_fn(f)``.

    Writes a temp file in the SAME directory, then ``os.replace``s it into place
    (an atomic rename on one filesystem). A process kill (SIGKILL/Ctrl-C), OOM, or
    ENOSPC mid-write leaves the previous file intact — the destination is
    untouched until the rename. This is NOT a power-loss durability guarantee:
    there is no fsync (matching the rest of the codebase), so an OS/hardware crash
    right after the rename can still expose unflushed bytes on some filesystems.
    The temp file is removed if the write fails.

    A symlinked destination is resolved first so the write goes THROUGH the link
    to its target (rather than replacing the link with a regular file), keeping
    the shared-output/worktree symlink setups this module documents working.
    """
    # Resolve symlinks so the temp lands on the target's filesystem (same-fs
    # atomic rename) and the replace writes through the link, not over it.
    real = Path(os.path.realpath(str(path)))
    real.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(real.parent), prefix=f".{real.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            write_fn(f)
        # mkstemp creates the temp file 0600; match the destination's existing
        # mode (or the umask default for a new file) so an atomic replace never
        # silently tightens a previously group/world-readable output to
        # owner-only. Best-effort — a chmod failure must not fail the write.
        try:
            mode = stat.S_IMODE(os.stat(real).st_mode)
        except OSError:
            umask = os.umask(0)
            os.umask(umask)
            mode = 0o666 & ~umask
        try:
            os.chmod(tmp, mode)
        except OSError:
            pass
        try:
            os.replace(tmp, str(real))
        except PermissionError:
            # Windows: os.replace fails (WinError 5/32) when the destination is
            # briefly locked by another handle (antivirus, an open reader). Fall
            # back to copy-then-delete, matching graphify.cache's atomic writer.
            import shutil
            shutil.copy2(tmp, str(real))
            os.unlink(tmp)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            # The temp was chmod'd to match the destination above, so when the
            # destination is read-only the temp is too — and Windows refuses to
            # unlink a read-only file. Clear the bit and retry, or every failed
            # write leaks a `.graph.json.*.tmp` into the output directory.
            try:
                os.chmod(tmp, stat.S_IWRITE)
                os.unlink(tmp)
            except OSError:
                pass
        raise


def write_text_atomic(path: "str | Path", text: str) -> None:
    """Atomically write ``text`` (UTF-8) to ``path``. See :func:`_atomic_replace`."""
    _atomic_replace(path, lambda f: f.write(text))


def write_json_atomic(path: "str | Path", obj, *, indent: "int | None" = None, ensure_ascii: bool = True) -> None:
    """Atomically write ``obj`` as JSON to ``path``, streaming the encode into the
    temp file rather than materializing the whole string first (matters for very
    large graphs). ``ensure_ascii`` mirrors ``json.dump`` so callers that emit raw
    UTF-8 (non-ASCII labels/paths) keep byte-for-byte output. See :func:`_atomic_replace`."""
    _atomic_replace(path, lambda f: json.dump(obj, f, indent=indent, ensure_ascii=ensure_ascii))


# Bare output-directory name. Used by path guards that walk parents looking for
# the output directory by name.
KG_OUT_NAME = os.path.basename(os.path.normpath(KG_OUT))


def out_path(*parts: str) -> Path:
    """A path inside the output dir, e.g. ``out_path("cache")``."""
    return Path(KG_OUT, *parts)


def default_graph_json() -> str:
    """Default ``graph.json`` path under the output dir.

    The package-wide fallback used by serve/build/benchmark and the read
    commands.
    """
    return str(out_path("graph.json"))


def is_absolute_any_platform(p: "str | Path | None") -> bool:
    """Whether *p* is absolute under POSIX **or** Windows rules.

    ``Path.is_absolute()`` and ``os.path.isabs()`` answer for the HOST os only,
    which is the wrong question for a path that was *stored* — a ``source_file``
    in ``graph.json``, a ``prune_sources`` entry, a cache key. Those travel
    between machines (build in Docker/CI, update on a Windows workstation, or
    the reverse), so the host's rules do not describe the string in hand:

    - On Windows, ``WindowsPath("/home/ci/repo/docs/a.md").is_absolute()`` is
      False — no drive letter — so a Linux-built graph's absolute paths read as
      relative and get baked into node IDs or joined under the scan root.
    - On POSIX, ``PosixPath("C:/Users/u/a.md").is_absolute()`` is False for the
      mirror-image reason.

    ``os.path.isabs`` is additionally not stable across supported interpreters:
    Python 3.13 changed ``ntpath.isabs`` so a path starting with a single slash
    is no longer absolute, where 3.10–3.12 said it was. The project supports
    >=3.10, so a guard written on it silently means different things per version.

    Answering for both platforms is the conservative choice for stored paths:
    treating a path as absolute at worst declines to relativize it (the string is
    kept as-is), whereas treating an absolute path as relative corrupts identity.
    Covers drive-letter, UNC, and POSIX-root forms with either separator.

    NOTE: this is for STORED/portable paths. Code resolving a path against the
    real local filesystem (``flow``, ``detect``) must keep using
    ``Path.is_absolute()`` — there the host's rules are exactly right.
    """
    if not p:
        return False
    s = str(p)
    return PurePosixPath(s).is_absolute() or PureWindowsPath(s).is_absolute()


def nfc(s: str) -> str:
    """NFC-normalize a path string.

    macOS (HFS+/APFS) reports filenames in NFD while manifests, graph
    ``source_file`` entries and user input are typically NFC. Comparing raw
    strings makes the same file look like two different paths, so any path
    membership test must normalize BOTH sides.
    """
    import unicodedata
    return unicodedata.normalize("NFC", s)


def load_node_link_graph(path_or_data):
    """Load a kg graph.json into a networkx graph, accepting both writers.

    The clustered writer stores edges under ``links`` (networkx's node-link
    default); the raw no-cluster writer stores them under ``edges``.
    Consumers that call ``node_link_graph(data, edges="links")`` directly
    raise ``KeyError: 'links'`` on a raw graph — the ``except
    TypeError`` fallback only covers old networkx without the ``edges``
    kwarg, not the missing key. Normalize before parsing, same idiom as
    affected.py/serve.py upstream.

    Accepts a path (size-cap-checked via the security module, then parsed)
    or an already-parsed dict (no size check — the caller owns any cap).
    """
    from networkx.readwrite import json_graph
    data = path_or_data
    if not isinstance(data, dict):
        p = Path(data)
        from kglib.security import check_graph_file_size_cap  # lazy: security imports paths
        check_graph_file_size_cap(p)
        data = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "links" not in data and "edges" in data:
        data = dict(data, links=data["edges"])
    try:
        return json_graph.node_link_graph(data, edges="links")
    except TypeError:  # networkx too old for the edges kwarg; default is "links"
        return json_graph.node_link_graph(data)
