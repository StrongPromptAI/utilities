"""Ingest markdown documentation repositories into doc_chunks.

Pattern: clone (shallow) → walk .md files → YAML-frontmatter + markdown-AST parse →
heading-aware chunk → upsert via crud.docs. Temp dir is wiped on exit unless
--keep is passed.

Usage from CLI:
    kb docs ingest --project openwebui-docs
"""
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

import yaml
from markdown_it import MarkdownIt

from ..crud.docs import get_or_create_project, upsert_doc_chunks, purge_stale


# ─── Known repos we know how to ingest ───────────────────────────────────────
# Maps project_name → (git URL, docs site base URL, subdir containing .md)

REPOS = {
    "openwebui-docs": {
        "git_url": "https://github.com/open-webui/docs.git",
        "site_base": "https://docs.openwebui.com",
        "docs_subdir": "docs",     # markdown lives under ./docs/** inside the repo
    },
}

CHUNK_TARGET_CHARS = 2000
CHUNK_OVERLAP_CHARS = 200


# ─── Frontmatter + heading helpers ───────────────────────────────────────────

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def strip_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter_dict, body_text). Empty dict if no frontmatter."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        fm = {}
    body = text[m.end():]
    return fm, body


def build_source_url(site_base: str, repo_path: str) -> str:
    """Translate docs/features/web-search/brave.md → https://.../features/web-search/brave
    (Docusaurus convention: strip .md / .mdx, drop /index if present, drop leading 'docs/')"""
    p = repo_path
    if p.endswith((".md", ".mdx")):
        p = p.rsplit(".", 1)[0]
    if p.endswith("/index"):
        p = p[:-len("/index")]
    if p.startswith("docs/"):
        p = p[len("docs/"):]
    return f"{site_base.rstrip('/')}/{p}"


# ─── Markdown → section-aware chunks ─────────────────────────────────────────

@dataclass
class Section:
    heading_path: list[str]     # accumulated heading ancestry
    body: str                   # section prose


def _iter_sections(md_body: str) -> Iterator[Section]:
    """Walk markdown, yield (heading_path, body) pairs at every heading boundary.
    Uses markdown-it-py tokens — robust against lists, code blocks, tables."""
    md = MarkdownIt("commonmark")
    tokens = md.parse(md_body)
    lines = md_body.splitlines()

    current_path: list[Optional[str]] = [None] * 7   # H1..H6, index 1..6
    section_start_line = 0

    def current_headings() -> list[str]:
        return [h for h in current_path if h]

    def emit(end_line: int):
        body = "\n".join(lines[section_start_line:end_line]).strip()
        if body:
            yield Section(heading_path=current_headings(), body=body)

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.type == "heading_open":
            # Close out the previous section at this line
            yield from emit(tok.map[0])
            level = int(tok.tag[1])         # h1 → 1
            # inline token is always the next one
            heading_text = tokens[i + 1].content.strip()
            # clear deeper levels, set this one
            for L in range(level, 7):
                current_path[L] = None
            current_path[level] = heading_text
            section_start_line = tok.map[1]  # lines right after the heading
        i += 1
    # tail
    yield from emit(len(lines))


def _chunk_section(body: str, target: int = CHUNK_TARGET_CHARS,
                   overlap: int = CHUNK_OVERLAP_CHARS) -> list[str]:
    """Split a section body into ~target-sized chunks with overlap. Prefer
    paragraph boundaries (\\n\\n); fall back to raw window if a paragraph
    is itself too big."""
    if len(body) <= target:
        return [body]
    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paragraphs:
        if len(buf) + len(p) + 2 <= target:
            buf = f"{buf}\n\n{p}" if buf else p
        else:
            if buf:
                chunks.append(buf)
            if len(p) > target:
                # paragraph alone exceeds target — window it
                start = 0
                while start < len(p):
                    chunks.append(p[start:start + target])
                    start += target - overlap
                buf = ""
            else:
                # start next chunk carrying tail of previous for overlap
                tail = buf[-overlap:] if buf else ""
                buf = f"{tail}\n\n{p}" if tail else p
    if buf:
        chunks.append(buf)
    return chunks


def _file_chunks(md_path: Path, repo_root: Path, site_base: str) -> list[dict]:
    """Parse one .md file → list of chunk dicts ready for upsert."""
    raw = md_path.read_text(encoding="utf-8")
    fm, body = strip_frontmatter(raw)
    if not body.strip():
        return []

    repo_path = str(md_path.relative_to(repo_root)).replace("\\", "/")
    source_url = build_source_url(site_base, repo_path)

    chunks: list[dict] = []
    idx = 0
    for section in _iter_sections(body):
        for piece in _chunk_section(section.body):
            chunks.append({
                "source_url": source_url,
                "repo_path": repo_path,
                "section_path": " > ".join(section.heading_path) or None,
                "chunk_idx": idx,
                "text": piece,
            })
            idx += 1
    return chunks


# ─── Top-level orchestrator ──────────────────────────────────────────────────

def _iter_markdown_files(root: Path) -> Iterator[Path]:
    for p in root.rglob("*.md"):
        yield p
    for p in root.rglob("*.mdx"):
        yield p


def ingest_project(project_name: str, keep_clone: Optional[str] = None,
                   limit_files: Optional[int] = None) -> dict:
    """Clone the docs repo for this project, walk markdown, upsert chunks,
    purge stale rows, and return a summary dict."""
    if project_name not in REPOS:
        raise ValueError(f"Unknown project '{project_name}'. Known: {list(REPOS)}")
    spec = REPOS[project_name]

    project_id = get_or_create_project(project_name)
    print(f"[ingest] project_id={project_id} name={project_name}")

    def _run(workdir: str) -> dict:
        subprocess.run(
            ["git", "clone", "--depth=1", spec["git_url"], workdir],
            check=True, capture_output=True,
        )
        repo_root = Path(workdir) / spec["docs_subdir"]
        if not repo_root.exists():
            # fall back: some repos keep markdown at root
            repo_root = Path(workdir)

        md_files = list(_iter_markdown_files(repo_root))
        if limit_files:
            md_files = md_files[:limit_files]
        print(f"[ingest] cloned; {len(md_files)} markdown files found")

        all_chunks: list[dict] = []
        seen_paths: set[str] = set()
        for md_path in md_files:
            file_chunks = _file_chunks(md_path, repo_root, spec["site_base"])
            all_chunks.extend(file_chunks)
            if file_chunks:
                seen_paths.add(file_chunks[0]["repo_path"])

        print(f"[ingest] {len(all_chunks)} total chunks; embedding + upserting...")
        written = upsert_doc_chunks(project_id, all_chunks)
        pruned = purge_stale(project_id, seen_paths)
        return {
            "files": len(md_files),
            "chunks_written": written,
            "chunks_pruned": pruned,
            "project_id": project_id,
        }

    if keep_clone:
        workdir = str(Path(keep_clone).expanduser())
        if Path(workdir).exists():
            shutil.rmtree(workdir)
        Path(workdir).mkdir(parents=True)
        result = _run(workdir)
        print(f"[ingest] clone kept at {workdir}")
        return result
    else:
        with tempfile.TemporaryDirectory(prefix=f"kbdocs-{project_name}-") as workdir:
            return _run(workdir)
