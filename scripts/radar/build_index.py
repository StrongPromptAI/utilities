"""
Build chunk-level semantic skill index from skill .md files.

Writes TWO indexes — bifurcated by content class:

- ~/.claude/radar_skills_wisdom.json — Layers 1 (global skills) + 4 (project
  wisdom docs). Narrative content; "what we've learned." Higher threshold
  in prompt_hook.py because the embed signal is strong.

- ~/.claude/radar_skills_what.json — Layer 3 (gitnexus-generated cluster
  SKILL.md files under each project's `.claude/skills/`). Structural
  digests; "what is." Lower threshold because key-files tables and symbol
  lists embed less semantically against natural-language prompts.

Both indexes share one manifest at ~/.claude/radar_skills_manifest.json:

    {
      "config_signature": "...",
      "files": {
        "wisdom": { "<path>": <mtime>, ... },
        "what":   { "<path>": <mtime>, ... }
      }
    }

Embed backend: see embed_client.py — local utilities ONNX service by default.

Usage:
    uv run python scripts/radar/build_index.py
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from embed_client import EMBED_URL, embed as _embed

print(f"[build_index] Embed backend: {EMBED_URL}")

REGISTRY = Path.home() / "repo_docs/skills/SKILL_REGISTRY.md"
INDEX_WISDOM = Path.home() / ".claude/radar_skills_wisdom.json"
INDEX_WHAT = Path.home() / ".claude/radar_skills_what.json"
MANIFEST_OUT = Path.home() / ".claude/radar_skills_manifest.json"
DOCUMENT_PREFIX = "search_document: "
MIN_CHUNK_CHARS = 80   # skip trivial stubs
MAX_CHUNK_CHARS = 1200  # truncate very long sections before embedding

# If any of these change, the whole cache is invalidated. v4 = bifurcated
# index files (was single skill_index.json in v3).
CONFIG_SIGNATURE = f"v4|min={MIN_CHUNK_CHARS}|max={MAX_CHUNK_CHARS}|prefix={DOCUMENT_PREFIX}|bifurcated"


def embed(texts: list[str]) -> list[list[float]]:
    """Delegate to the Skill Radar embed client."""
    return _embed(texts, timeout=30.0)


def parse_registry(path: Path) -> list[dict]:
    """Extract skill name + file path from SKILL_REGISTRY.md table rows.

    Stops at the Project Skill Trees section — those rows are walked
    separately by walk_project_tree().
    """
    skills = []
    text = path.read_text()

    trees_marker = re.search(r"^##\s+Project Skill Trees", text, re.MULTILINE)
    if trees_marker:
        text = text[: trees_marker.start()]

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


def parse_project_trees(text: str) -> list[dict]:
    """Parse the Project Skill Trees section: list of {root, skip_dirs}."""
    section_re = re.search(
        r"^##\s+Project Skill Trees.*?(?=^##\s|\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if not section_re:
        return []
    section = section_re.group(0)

    row_re = re.compile(
        r"^\|\s*`([^`|]+)`\s*\|\s*`?([^`|]*)`?\s*\|\s*([^|]+)\|",
        re.MULTILINE,
    )
    trees = []
    for m in row_re.finditer(section):
        root = m.group(1).strip()
        skip_raw = m.group(2).strip()
        note = m.group(3).strip()
        if not root.startswith("~/"):
            continue
        skip_dirs = set()
        if skip_raw and skip_raw not in ("—", "-"):
            skip_dirs = {s.strip() for s in skip_raw.split(",") if s.strip()}
        trees.append({"root": root, "skip_dirs": skip_dirs, "note": note})
    return trees


def walk_project_tree(tree: dict) -> list[dict]:
    """Glob <root>/**/SKILL.md and return synthetic skill records.

    skill_name = `<project>:<dir-or-frontmatter-name>` for collision safety.
    """
    root = Path(tree["root"].replace("~/", str(Path.home()) + "/"))
    if not root.exists() or not root.is_dir():
        print(f"  [project tree] root not found, skipping: {tree['root']}")
        return []

    project = "unknown"
    cursor = root
    while cursor.parent != cursor:
        if cursor.name == ".claude":
            project = cursor.parent.name
            break
        cursor = cursor.parent

    skip_dirs: set[str] = tree["skip_dirs"]
    fallback_desc = tree["note"]

    found = []
    for skill_md in root.rglob("SKILL.md"):
        rel_parts = skill_md.relative_to(root).parts
        if any(part in skip_dirs for part in rel_parts):
            continue

        cluster = skill_md.parent.name
        text = skill_md.read_text()
        fm_name = None
        fm_desc = None
        fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
        if fm_match:
            fm = fm_match.group(1)
            n = re.search(r"^name:\s*(.+?)\s*$", fm, re.MULTILINE)
            d = re.search(r"^description:\s*[\"']?(.+?)[\"']?\s*$", fm, re.MULTILINE)
            if n:
                fm_name = n.group(1).strip()
            if d:
                fm_desc = d.group(1).strip()

        skill_name = f"{project}:{fm_name or cluster}"
        path_str = str(skill_md).replace(str(Path.home()), "~", 1)
        description = fm_desc or fallback_desc or f"Project skill for {project} {cluster}"

        found.append({
            "name": skill_name,
            "path": path_str,
            "description": description,
        })

    return found


def resolve_path(path_str: str) -> Path | None:
    p = Path(path_str.replace("~/", str(Path.home()) + "/"))
    return p if p.exists() else None


def split_into_chunks(text: str, skill_name: str, file_path: str, load_when: str = "") -> list[dict]:
    """Split markdown into sections by ## / ### headers."""
    header_re = re.compile(r"^(#{2,3})\s+(.+)$", re.MULTILINE)
    kw_line = f"\nLoad when: {load_when}" if load_when else ""

    matches = list(header_re.finditer(text))
    if not matches:
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


def file_mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def load_prior_manifest() -> dict:
    """Load the unified manifest. Empty if config signature changed or files missing."""
    if not MANIFEST_OUT.exists():
        return {}
    try:
        manifest = json.loads(MANIFEST_OUT.read_text())
    except Exception:
        return {}
    if manifest.get("config_signature") != CONFIG_SIGNATURE:
        print(f"  Config signature changed — full rebuild.")
        return {}
    return manifest


def load_prior_index(index_path: Path) -> dict[tuple[str, str], list[dict]]:
    """Load a prior index keyed by (file_path, skill_name)."""
    if not index_path.exists():
        return {}
    try:
        prior = json.loads(index_path.read_text())
    except Exception:
        return {}
    by_key: dict[tuple[str, str], list[dict]] = {}
    for entry in prior:
        key = (entry.get("file_path", ""), entry.get("skill_name", ""))
        by_key.setdefault(key, []).append(entry)
    return by_key


def build_dimension(
    dim_name: str,
    skills: list[dict],
    prior_by_file: dict[tuple[str, str], list[dict]],
    prior_files: dict[str, float],
) -> tuple[list[dict], dict[str, float]]:
    """Build one dimension's index entries. Returns (index_entries, new_files_map).

    Reuses unchanged file embeddings; freshly embeds new/changed files.
    """
    all_chunks: list[dict] = []
    reused_entries: list[dict] = []
    new_files: dict[str, float] = {}
    skipped = 0
    reused_files = 0

    for skill in skills:
        fpath = resolve_path(skill["path"])

        if fpath is None:
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
        prior_mtime = prior_files.get(str(fpath), 0.0)
        new_files[str(fpath)] = current_mtime

        reuse_key = (skill["path"], skill["name"])
        if (
            current_mtime
            and current_mtime == prior_mtime
            and reuse_key in prior_by_file
        ):
            reused_entries.extend(prior_by_file.pop(reuse_key))
            reused_files += 1
            continue

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
        f"  [{dim_name}] {len(skills)} skills | reuse {reused_files} files "
        f"({len(reused_entries)} chunks) | fresh {len(skills) - reused_files} files "
        f"({len(all_chunks)} chunks, {skipped} description fallback)"
    )

    if not all_chunks:
        return reused_entries, new_files

    # Embed fresh chunks in batches
    batch_size = 20
    fresh_embeddings: list[list[float]] = []
    for i in range(0, len(all_chunks), batch_size):
        batch = all_chunks[i : i + batch_size]
        texts = [c["embed_text"] for c in batch]
        vecs = embed(texts)
        fresh_embeddings.extend(vecs)
        print(f"    [{dim_name}] embedded {min(i + batch_size, len(all_chunks))}/{len(all_chunks)}")

    index = list(reused_entries)
    for chunk, vec in zip(all_chunks, fresh_embeddings):
        entry = {k: v for k, v in chunk.items() if k != "embed_text"}
        entry["embedding"] = vec
        index.append(entry)

    return index, new_files


def main():
    if not REGISTRY.exists():
        print(f"ERROR: SKILL_REGISTRY.md not found at {REGISTRY}", file=sys.stderr)
        sys.exit(1)

    prior_manifest = load_prior_manifest()
    prior_files_by_dim = prior_manifest.get("files", {})
    prior_files_wisdom = prior_files_by_dim.get("wisdom", {})
    prior_files_what = prior_files_by_dim.get("what", {})

    # Registry mtime check — if changed, blow the wisdom cache (it's the
    # source-of-truth for wisdom skills). The what cache stays valid because
    # tree walking is filesystem-driven, independent of registry rows.
    registry_mtime = file_mtime(REGISTRY)
    registry_changed = prior_files_wisdom.get(str(REGISTRY), 0.0) != registry_mtime
    prior_wisdom_idx = {} if registry_changed else load_prior_index(INDEX_WISDOM)
    prior_what_idx = load_prior_index(INDEX_WHAT)
    if registry_changed and prior_files_wisdom:
        print("  SKILL_REGISTRY.md changed — re-scanning all wisdom files.")

    # Wisdom skills: registry table rows
    wisdom_skills = parse_registry(REGISTRY)
    print(f"Parsed {len(wisdom_skills)} wisdom skills from registry")

    # What skills: walk project trees
    registry_text = REGISTRY.read_text()
    trees = parse_project_trees(registry_text)
    what_skills: list[dict] = []
    for tree in trees:
        found = walk_project_tree(tree)
        print(f"  [project tree] {tree['root']}: {len(found)} skills")
        what_skills.extend(found)
    print(f"Walked {len(what_skills)} 'what' skills from project trees")

    # Lint duplicates per-dimension
    from collections import Counter
    lint_errors: list[str] = []
    for label, pool in (("wisdom", wisdom_skills), ("what", what_skills)):
        name_counts = Counter(s["name"].lower() for s in pool)
        path_counts = Counter(s["path"] for s in pool)
        for name, count in name_counts.items():
            if count > 1:
                lint_errors.append(f"[{label}] Duplicate skill name: {name!r} appears {count} times")
        for path, count in path_counts.items():
            if count > 1:
                names = [s["name"] for s in pool if s["path"] == path]
                lint_errors.append(f"[{label}] Duplicate path {path}: {names}")
    if lint_errors:
        print("ERROR: registry lint failures —", file=sys.stderr)
        for err in lint_errors:
            print(f"  {err}", file=sys.stderr)
        sys.exit(1)

    # Warmup embed service before per-dimension batches
    try:
        embed(["warmup"])
    except Exception as e:
        print(f"ERROR: embed service not reachable: {e}", file=sys.stderr)
        sys.exit(1)

    wisdom_index, wisdom_files = build_dimension(
        "wisdom", wisdom_skills, prior_wisdom_idx, prior_files_wisdom
    )
    what_index, what_files = build_dimension(
        "what", what_skills, prior_what_idx, prior_files_what
    )

    # Registry mtime is a wisdom-side dependency; record under wisdom files.
    wisdom_files[str(REGISTRY)] = registry_mtime

    INDEX_WISDOM.parent.mkdir(parents=True, exist_ok=True)
    INDEX_WISDOM.write_text(json.dumps(wisdom_index, separators=(",", ":")))
    INDEX_WHAT.write_text(json.dumps(what_index, separators=(",", ":")))

    MANIFEST_OUT.write_text(json.dumps({
        "config_signature": CONFIG_SIGNATURE,
        "files": {
            "wisdom": wisdom_files,
            "what": what_files,
        },
    }, indent=2))

    print(f"Wrote {len(wisdom_index)} wisdom chunks → {INDEX_WISDOM}")
    print(f"Wrote {len(what_index)} 'what' chunks → {INDEX_WHAT}")
    print(f"Manifest: {MANIFEST_OUT}")


if __name__ == "__main__":
    main()
