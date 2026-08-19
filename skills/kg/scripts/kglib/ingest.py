# Vendored from graphify (https://github.com/safishamsi/graphify), slimmed to documents-only.
# Query-result persistence ("work memory"). Upstream's URL ingest (fetch webpage /
# tweet / arxiv into a raw/ folder) is dropped — kg is documents-only and never
# fetches from the network.
from __future__ import annotations
import re
from datetime import datetime, timezone
from pathlib import Path


def _yaml_str(s: str) -> str:
    """Escape a value for safe embedding in a YAML double-quoted scalar (F-009).

    A hostile or merely punctuated question/answer (quotes, backslashes, line
    breaks, U+2028/U+2029, control characters) must not be able to break out of
    the YAML scalar and inject sibling frontmatter keys — the memory doc is
    re-read by kg's reflect pass and re-extracted into the graph.
    """
    if s is None:
        return ""
    out: list[str] = []
    for ch in str(s):
        cp = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\0":
            out.append("\\0")
        elif cp == 0x2028:
            out.append("\\L")
        elif cp == 0x2029:
            out.append("\\P")
        elif cp < 0x20 or cp == 0x7F:
            out.append(f"\\x{cp:02x}")
        else:
            out.append(ch)
    return "".join(out)


OUTCOMES = ("useful", "dead_end", "corrected")


def save_query_result(
    question: str,
    answer: str,
    memory_dir: Path,
    query_type: str = "query",
    source_nodes: list[str] | None = None,
    outcome: str | None = None,
    correction: str | None = None,
) -> Path:
    """Save a Q&A result as markdown so it gets extracted into the graph on next update.

    Files are stored in memory_dir (typically kg-out/memory/) with YAML frontmatter
    that the extractor reads as node metadata. This closes the feedback loop:
    the system grows smarter from both what you add AND what you ask.

    ``outcome`` (one of :data:`OUTCOMES`) and ``correction`` are optional work-memory
    signals: they are written both to the frontmatter (so `kg reflect` can
    aggregate them deterministically) and to an ``## Outcome`` body section (so the
    signal round-trips into the graph on the next semantic re-extraction).
    """
    if outcome is not None and outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {OUTCOMES}, got {outcome!r}")

    memory_dir = Path(memory_dir)
    memory_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    slug = re.sub(r"[^\w]", "_", question.lower())[:50].strip("_")
    filename = f"query_{now.strftime('%Y%m%d_%H%M%S')}_{slug}.md"

    frontmatter_lines = [
        "---",
        f'type: "{query_type}"',
        f'date: "{now.isoformat()}"',
        f'question: "{_yaml_str(question)}"',
        'contributor: "kg"',
    ]
    if outcome:
        frontmatter_lines.append(f'outcome: "{_yaml_str(outcome)}"')
    if correction:
        frontmatter_lines.append(f'correction: "{_yaml_str(correction)}"')
    if source_nodes:
        nodes_str = ", ".join(f'"{_yaml_str(n)}"' for n in source_nodes[:10])
        frontmatter_lines.append(f"source_nodes: [{nodes_str}]")
    frontmatter_lines.append("---")

    body_lines = [
        "",
        f"# Q: {question}",
        "",
        "## Answer",
        "",
        answer,
    ]
    if outcome or correction:
        body_lines += ["", "## Outcome", ""]
        if outcome:
            body_lines.append(f"- Signal: {outcome}")
        if correction:
            body_lines.append(f"- Correction: {correction}")
    if source_nodes:
        body_lines += ["", "## Source Nodes", ""]
        body_lines += [f"- {n}" for n in source_nodes]

    content = "\n".join(frontmatter_lines + body_lines)
    out_path = memory_dir / filename
    out_path.write_text(content, encoding="utf-8")
    return out_path
