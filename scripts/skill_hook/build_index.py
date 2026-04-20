"""
Build chunk-level semantic skill index from skill .md files.

Parses SKILL_REGISTRY.md to get skill file paths, reads each skill file,
splits by ## / ### headers into sections, embeds each section, and writes
~/.claude/skill_index.json with one entry per chunk (not per skill).

Run once after setup, then re-run whenever skill files change.

Usage:
    uv run python scripts/skill_hook/build_index.py
"""

import json
import re
import sys
from pathlib import Path

# Local import — shared-svcs embed client (Railway-hosted, JWT-authed).
sys.path.insert(0, str(Path(__file__).parent))
from embed_client import embed as _embed

REGISTRY = Path.home() / "repo_docs/skills/SKILL_REGISTRY.md"
INDEX_OUT = Path.home() / ".claude/skill_index.json"
MANIFEST_OUT = Path.home() / ".claude/skill_index_manifest.json"
DOCUMENT_PREFIX = "search_document: "
MIN_CHUNK_CHARS = 80   # skip trivial stubs
MAX_CHUNK_CHARS = 1200  # truncate very long sections before embedding

# If any of these change, the whole cache is invalidated.
# Bump by editing one of the values above — or by editing this signature directly
# when you change chunking or embedding semantics without changing a constant.
CONFIG_SIGNATURE = f"v3|min={MIN_CHUNK_CHARS}|max={MAX_CHUNK_CHARS}|prefix={DOCUMENT_PREFIX}"


def embed(texts: list[str]) -> list[list[float]]:
    """Delegate to shared-svcs embed client (Railway-hosted, JWT-authed)."""
    return _embed(texts, timeout=30.0)


def parse_registry(path: Path) -> list[dict]:
    """Extract skill name + file path from SKILL_REGISTRY.md table rows."""
    skills = []
    text = path.read_text()

    row_re = re.compile(
        r"^\|\s*\*{0,2}([^|*`]+)\*{0,2}\s*\|\s*`?([^|`]+)`?\s*\|\s*([^|]+)\|?",
        re.MULTILINE,
    )

    seen = set()
    for m in row_re.finditer(text):
        name = m.group(1).strip()
        path_str = m.group(2).strip()
        description = m.group(3).strip()

        if name.lower() in ("skill", "subskill", "source", "use for", "trigger words", "name", "---"):
            continue
        if set(name) <= set("- "):
            continue
        if not path_str.startswith("~/") and "SKILL" not in path_str and ".md" not in path_str:
            continue

        key = name.lower()
        if key in seen:
            continue
        seen.add(key)

        skills.append({"name": name, "path": path_str, "description": description})

    return skills


def resolve_path(path_str: str) -> Path | None:
    """Resolve ~/... paths to absolute paths."""
    p = Path(path_str.replace("~/", str(Path.home()) + "/"))
    return p if p.exists() else None


def split_into_chunks(text: str, skill_name: str, file_path: str, load_when: str = "") -> list[dict]:
    """Split markdown into sections by ## / ### headers."""
    header_re = re.compile(r"^(#{2,3})\s+(.+)$", re.MULTILINE)

    # Keyword line injected into every chunk's embed_text so the registry's
    # "Load When" triggers survive into the embedding vector.
    kw_line = f"\nLoad when: {load_when}" if load_when else ""

    matches = list(header_re.finditer(text))
    if not matches:
        # No headers — treat whole file as one chunk
        content = text.strip()
        if len(content) >= MIN_CHUNK_CHARS:
            return [{
                "skill_name": skill_name,
                "file_path": file_path,
                "header": skill_name,
                "load_when": load_when,
                "text": content[:MAX_CHUNK_CHARS],
            }]
        return []

    chunks = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[start:end].strip()

        if len(content) < MIN_CHUNK_CHARS:
            continue

        header = m.group(2).strip()
        embed_text = f"{skill_name} — {header}{kw_line}\n\n{content[:MAX_CHUNK_CHARS]}"

        chunks.append({
            "skill_name": skill_name,
            "file_path": file_path,
            "header": header,
            "load_when": load_when,
            "text": content[:MAX_CHUNK_CHARS],
            "embed_text": embed_text,
        })

    return chunks


def load_prior_index_and_manifest() -> tuple[dict[str, list[dict]], dict]:
    """Load the prior index keyed by file_path and the manifest.

    Returns ({file_path: [chunk_entries_with_embedding]}, manifest_dict).
    If config signature changed, returns empty (full rebuild).
    If either file is missing or malformed, returns empty.
    """
    if not INDEX_OUT.exists() or not MANIFEST_OUT.exists():
        return {}, {}

    try:
        manifest = json.loads(MANIFEST_OUT.read_text())
    except Exception:
        return {}, {}

    if manifest.get("config_signature") != CONFIG_SIGNATURE:
        print(f"  Config signature changed — full rebuild.")
        return {}, {}

    try:
        prior_index = json.loads(INDEX_OUT.read_text())
    except Exception:
        return {}, {}

    # Group by (file_path, skill_name) — two skills can share a file path
    # (e.g., a skill and a subskill both pointing at the same SKILL.md),
    # so file_path alone is not a unique bucket key.
    by_key: dict[tuple[str, str], list[dict]] = {}
    for entry in prior_index:
        key = (entry.get("file_path", ""), entry.get("skill_name", ""))
        by_key.setdefault(key, []).append(entry)
    return by_key, manifest


def file_mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def main():
    if not REGISTRY.exists():
        print(f"ERROR: SKILL_REGISTRY.md not found at {REGISTRY}", file=sys.stderr)
        sys.exit(1)

    # Registry itself is a dependency — if it changes, we re-scan everything
    # (it may have added/removed rows, which can't be detected file-by-file).
    prior_by_file, prior_manifest = load_prior_index_and_manifest()
    registry_mtime = file_mtime(REGISTRY)
    registry_changed = (
        prior_manifest.get("files", {}).get(str(REGISTRY), 0.0) != registry_mtime
    )
    if registry_changed and prior_by_file:
        print("  SKILL_REGISTRY.md changed — re-scanning all files.")
        prior_by_file = {}

    skills = parse_registry(REGISTRY)
    print(f"Parsed {len(skills)} skills from registry")

    # Registry lint: catch duplicate names and duplicate paths at build time.
    # Duplicate names would be silently deduped by parse_registry; duplicate
    # paths (different names pointing to the same file) pollute the semantic
    # index with identical chunks under different skill_name labels and break
    # incremental reuse without the (file_path, skill_name) key workaround.
    # Both are registry-quality bugs, not runtime bugs — fail early.
    from collections import Counter
    name_counts = Counter(s["name"].lower() for s in skills)
    path_counts = Counter(s["path"] for s in skills)
    lint_errors: list[str] = []
    for name, count in name_counts.items():
        if count > 1:
            lint_errors.append(f"Duplicate skill name: {name!r} appears {count} times")
    for path, count in path_counts.items():
        if count > 1:
            names = [s["name"] for s in skills if s["path"] == path]
            lint_errors.append(f"Duplicate path {path}: {names}")
    if lint_errors:
        print("ERROR: registry lint failures —", file=sys.stderr)
        for err in lint_errors:
            print(f"  {err}", file=sys.stderr)
        print(f"Fix in {REGISTRY}", file=sys.stderr)
        sys.exit(1)

    all_chunks: list[dict] = []  # need (re-)embedding
    reused_entries: list[dict] = []  # reuse embedding from prior index
    new_manifest_files: dict[str, float] = {str(REGISTRY): registry_mtime}
    skipped = 0
    reused_files = 0

    for skill in skills:
        fpath = resolve_path(skill["path"])

        if fpath is None:
            # No resolvable file — description fallback, always fresh embed
            all_chunks.append({
                "skill_name": skill["name"],
                "file_path": skill["path"],
                "header": skill["name"],
                "load_when": skill["description"],
                "text": skill["description"],
                "embed_text": DOCUMENT_PREFIX + skill["name"] + " — " + skill["description"],
            })
            skipped += 1
            continue

        current_mtime = file_mtime(fpath)
        prior_mtime = prior_manifest.get("files", {}).get(str(fpath), 0.0)
        new_manifest_files[str(fpath)] = current_mtime

        # Reuse path: file unchanged AND we have prior chunks for this
        # (file_path, skill_name) pair. Two skills can share a file — each
        # has its own bucket keyed by skill_name too.
        reuse_key = (skill["path"], skill["name"])
        if (
            current_mtime
            and current_mtime == prior_mtime
            and reuse_key in prior_by_file
        ):
            reused_entries.extend(prior_by_file.pop(reuse_key))
            reused_files += 1
            continue

        # Fresh embed path
        text = fpath.read_text()
        chunks = split_into_chunks(text, skill["name"], skill["path"], skill["description"])
        if not chunks:
            all_chunks.append({
                "skill_name": skill["name"],
                "file_path": skill["path"],
                "header": skill["name"],
                "load_when": skill["description"],
                "text": skill["description"],
                "embed_text": DOCUMENT_PREFIX + skill["name"] + " — " + skill["description"],
            })
            skipped += 1
        else:
            for c in chunks:
                if "embed_text" not in c:
                    c["embed_text"] = DOCUMENT_PREFIX + c["skill_name"] + " — " + c["header"] + "\n\n" + c["text"]
                else:
                    c["embed_text"] = DOCUMENT_PREFIX + c["embed_text"]
            all_chunks.extend(chunks)

    print(
        f"Reuse: {reused_files} files ({len(reused_entries)} chunks) — "
        f"Fresh: {len(skills) - reused_files} files ({len(all_chunks)} chunks, "
        f"{skipped} description fallback)"
    )

    # Warmup embed service only if we have anything to embed
    if all_chunks:
        try:
            embed(["warmup"])
        except Exception as e:
            print(f"ERROR: shared-svcs embed not reachable: {e}", file=sys.stderr)
            sys.exit(1)

    # Embed only the fresh chunks
    batch_size = 20
    fresh_embeddings = []
    for i in range(0, len(all_chunks), batch_size):
        batch = all_chunks[i : i + batch_size]
        texts = [c["embed_text"] for c in batch]
        vecs = embed(texts)
        fresh_embeddings.extend(vecs)
        print(f"  Embedded {min(i + batch_size, len(all_chunks))}/{len(all_chunks)}")

    # Build the final index: reused entries + fresh entries
    index = list(reused_entries)
    for chunk, vec in zip(all_chunks, fresh_embeddings):
        entry = {k: v for k, v in chunk.items() if k != "embed_text"}
        entry["embedding"] = vec
        index.append(entry)

    INDEX_OUT.parent.mkdir(parents=True, exist_ok=True)
    INDEX_OUT.write_text(json.dumps(index, separators=(",", ":")))

    MANIFEST_OUT.write_text(json.dumps({
        "config_signature": CONFIG_SIGNATURE,
        "files": new_manifest_files,
    }, indent=2))

    print(f"Written {len(index)} chunk entries to {INDEX_OUT}")
    print(f"Manifest: {MANIFEST_OUT}")


if __name__ == "__main__":
    main()
