"""
Schema-comment corpus for the radar (plan thj/26-6-16).

Parses ``COMMENT ON TABLE`` / ``COMMENT ON COLUMN`` statements out of a
committed ``schema.sql`` dump and renders one chunk per commented table. This is
the SHARED parse/render/registry layer used by three callers — one source of
truth for "what comments exist":

  - ``build_schema_index.py``  — embed the chunks into a per-repo index.
  - ``radar_prompt.py``        — match a prompt against the index and inject.
  - ``radar_post_tool.py``     — look up one table's comment on a schema edit.

Source of truth is the committed ``schema.sql`` (rewritten on every push), NOT a
live DB query. The radar wants durable semantic context (what a table IS, why
it's shaped that way), not the five-minutes-ago field — and a live-query hook
would silently no-op headless with no ``DATABASE_URL``. See the plan § "What
this is NOT" for the source ruling.

Repo registration: ``~/.claude/radar_schema_repos.json`` maps a repo slug to its
repo root + schema.sql path. That pointer is the only hand-maintained datum; the
comment TEXT is always read from schema.sql at build time.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# A table comment below this is too thin to carry durable semantic context;
# skip it (the corpus is self-limiting to tables that say something). A table
# with no table-comment but ≥1 column comment is still kept.
MIN_TABLE_COMMENT_CHARS = 40
# Truncate a rendered chunk before embedding/injection (the residency comments
# run long; the head carries the load-bearing meaning).
MAX_CHUNK_CHARS = 1500

# Bump when the parse/render shape changes so a stale index rebuilds.
CONFIG_SIGNATURE = "schema-v1|min=40|max=1500|prefix=search_document"

REPOS_REGISTRY = Path.home() / ".claude" / "radar_schema_repos.json"

# Postgres dump form: COMMENT ON TABLE public.t IS '...'; / COMMENT ON COLUMN
# public.t.c IS '...';  — single-quoted, '' -escaped, value may span lines.
# [^'] in the body already spans newlines (negated class), so no DOTALL needed.
_COMMENT_RE = re.compile(
    r"COMMENT\s+ON\s+(TABLE|COLUMN)\s+([A-Za-z0-9_.\"]+)\s+IS\s+'((?:[^']|'')*)'",
    re.IGNORECASE,
)


def expand(p: str) -> str:
    """Expand a leading ``~`` to the home dir (registry paths use ~)."""
    if p.startswith("~"):
        return str(Path.home()) + p[1:]
    return p


def _unescape(s: str) -> str:
    return s.replace("''", "'")


def _table_col(kind: str, obj: str) -> tuple[str, str | None]:
    """Resolve a dotted object name to (table, column|None) using the statement
    KIND to disambiguate ``schema.table`` (TABLE) from ``table.column`` (COLUMN).
    Strips the schema qualifier and any quoting."""
    parts = [p.strip('"') for p in obj.split(".")]
    if kind.upper() == "COLUMN":
        # schema.table.column  OR  table.column
        return parts[-2], parts[-1]
    # schema.table  OR  table
    return parts[-1], None


def parse_comments(path: str) -> dict[str, dict]:
    """Parse a schema.sql into ``{table: {"table": <comment>, "columns": {col: comment}}}``.

    Keyed by BARE table name (schema stripped). When a table is commented in
    both ``public`` and ``sandbox`` (the dump emits public first), the first
    table-comment and each first column-comment win — the public residency
    statement is the one we want. Pure text parse; never touches a DB."""
    text = Path(expand(path)).read_text()
    out: dict[str, dict] = {}
    for m in _COMMENT_RE.finditer(text):
        kind, obj, raw = m.group(1), m.group(2), m.group(3)
        table, col = _table_col(kind, obj)
        rec = out.setdefault(table, {"table": "", "columns": {}})
        comment = _unescape(raw).strip()
        if col is None:
            if not rec["table"]:
                rec["table"] = comment
        else:
            rec["columns"].setdefault(col, comment)
    return out


def is_substantive(rec: dict) -> bool:
    return len(rec.get("table", "")) >= MIN_TABLE_COMMENT_CHARS or bool(rec.get("columns"))


def render_chunk(table: str, rec: dict) -> str:
    """Render one table's comments as a labeled block (display + embed base)."""
    lines = [f"{table}: {rec['table']}" if rec.get("table") else f"{table}:"]
    for col, c in rec.get("columns", {}).items():
        lines.append(f"· {col}: {c}")
    return "\n".join(lines)[:MAX_CHUNK_CHARS]


def build_chunks(path: str) -> list[dict]:
    """Return ``[{"table", "text"}]`` for every substantive-comment table, sorted."""
    parsed = parse_comments(path)
    return [
        {"table": t, "text": render_chunk(t, rec)}
        for t, rec in sorted(parsed.items())
        if is_substantive(rec)
    ]


def lookup(path: str, table: str) -> str | None:
    """Render one table's chunk by name, or None if it has no substantive comment.
    Used by the schema-on-code redirect."""
    parsed = parse_comments(path)
    rec = parsed.get(table)
    if rec is None or not is_substantive(rec):
        return None
    return render_chunk(table, rec)


# ── Per-repo registration + index paths ──────────────────────────────────────

def load_repos() -> dict:
    """Load the repo-slug → {repo_root, schema_sql} registry. Empty on any error."""
    try:
        return json.loads(REPOS_REGISTRY.read_text())
    except Exception:
        return {}


def repo_for_cwd(cwd: str) -> tuple[str | None, dict | None]:
    """Resolve a working directory to (repo_slug, cfg) via the registry's
    repo_root prefixes. Returns (None, None) if cwd isn't in a registered repo."""
    cwd = cwd.rstrip("/")
    for slug, cfg in load_repos().items():
        root = expand(cfg.get("repo_root", "")).rstrip("/")
        if root and (cwd == root or cwd.startswith(root + "/")):
            return slug, cfg
    return None, None


def index_path(slug: str) -> Path:
    return Path.home() / ".claude" / f"radar_schema_{slug}.json"


def manifest_path(slug: str) -> Path:
    return Path.home() / ".claude" / f"radar_schema_{slug}_manifest.json"
