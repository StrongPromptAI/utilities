"""Outline-driven meeting summary (plan 26-5-21).

Workflow:
  1. Load user's outline from `outlines` (errors if missing)
  2. Load chunks from `call_chunks` ordered by chunk_idx
  3. Format transcript as `Speaker: text` lines — NO timestamps in payload
     (sequencing rule #4 in plan; timestamps stay in DB for navigation only)
  4. If `phi=True`: scrub the transcript text via Presidio, keep a token map
  5. Call `complete_with_fallback()` — primary (Opus 4.7) with Gemini Flash
     backup on transient errors
  6. If scrubbed: rehydrate the LLM output before storing
  7. Insert into `meeting_summaries` and return the new row id

Public entry: `generate_summary(call_id, *, phi=False, model="primary")`.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Tuple

import psycopg
from psycopg.rows import dict_row

from .config import DB_URL
from .llm import complete_with_fallback


PROMPT_TEMPLATE = """You are a meeting note-taker producing a high-quality summary of a real business meeting.

Output format — markdown only, no preamble:

## Participants
Brief one-line per participant — role/context if inferable from the conversation.

## Decisions & Themes
3-7 cross-cutting themes that span multiple parts of the meeting. For each:

### <Theme name>
- Concrete supporting bullets — commitments, numbers, names, decisions, blockers, specific details
- [Speaker Name: "verbatim quote from the transcript"]

## Action items
Specific commitments made during the call. One bullet per item:
- **<Who>** will <what>, by <when if stated>. <Brief context or blocker if stated>. [Speaker: "verbatim quote where committed"]

## Open threads
Anything left unresolved — questions raised but not answered, dependencies waiting on someone, follow-ups mentioned but not pinned to an owner.

Rules:
- Themes must capture cross-meeting patterns, not chunk-local summaries.
- Supporting bullets must preserve concrete detail: numbers, names, commitments, dates, blockers.
- Quotes must be VERBATIM. Do not paraphrase. 1-3 per theme, picking the most load-bearing or revealing lines.
- Speaker attribution must match the transcript exactly.
- Action items must reflect explicit commitments (someone said "I will" or equivalent). Don't promote vague mentions to action items.
- No invented content. If something is unclear, omit it rather than guess.

Transcript (Speaker: utterance, one turn per line):

{transcript}
"""


def _format_transcript(rows: list[dict]) -> str:
    """Format chunks for LLM payload — Speaker: text, no timestamps."""
    return "\n".join(f"{r['speaker'] or 'Unknown'}: {r['text']}" for r in rows)


# Inline directive: a line `@include <path>` in a lens file is replaced by that
# file's contents. Paths resolve relative to the lens file first, then as-is.
_INCLUDE_RE = re.compile(r'^@include\s+(.+)$', re.MULTILINE)


def _build_lens_prompt(lens_path: str, transcript: str) -> str:
    """Build a lens-driven prompt.

    The lens file is the full instruction (priming + objective + output contract
    + rules). `@include <path>` lines are inlined verbatim — that's how priming
    context docs (stakeholder/project docs) are placed "top of mind". The
    transcript is substituted at `{transcript}` if present, else appended.

    Genericity: the engine carries no per-purpose logic — swap the lens file and
    the output shape changes. NOT .format() — lens/doc text may contain literal
    braces; only the exact `{transcript}` token is substituted (via .replace).
    """
    lens_file = Path(lens_path)
    text = lens_file.read_text()

    def _inline(m: re.Match) -> str:
        raw = m.group(1).strip()
        p = lens_file.parent / raw
        if not p.exists():
            p = Path(raw)
        if not p.exists():
            raise FileNotFoundError(f"Lens @include not found: {raw!r} (in {lens_path})")
        body = p.read_text()
        return f"\n<<< BEGIN {p.name} >>>\n{body}\n<<< END {p.name} >>>\n"

    text = _INCLUDE_RE.sub(_inline, text)

    if "{transcript}" in text:
        return text.replace("{transcript}", transcript)
    return (
        text
        + "\n\nTranscript (Speaker: utterance, one turn per line):\n\n"
        + transcript
    )


def _load_chunks(call_id: int) -> list[dict]:
    """Return chunk rows for a call, ordered. Raises if call has no chunks."""
    with psycopg.connect(DB_URL, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT speaker, text FROM call_chunks "
                "WHERE call_id = %s ORDER BY chunk_idx",
                (call_id,),
            )
            chunks = list(cur.fetchall())
            if not chunks:
                raise ValueError(f"No chunks for call_id={call_id}.")
    return chunks


def _persist_summary(
    call_id: int,
    content: str,
    model_used: str,
    phi_scrubbed: bool,
    lens: str | None = None,
) -> int:
    """Insert into meeting_summaries; return new id."""
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO meeting_summaries "
                "(call_id, outline_id, content, model_used, phi_scrubbed, lens) "
                "VALUES (%s, NULL, %s, %s, %s, %s) RETURNING id",
                (call_id, content, model_used, phi_scrubbed, lens),
            )
            new_id = cur.fetchone()[0]
            conn.commit()
    return new_id


def generate_summary(
    call_id: int,
    *,
    phi: bool = False,
    max_tokens: int = 8000,
    lens_path: str | None = None,
) -> int:
    """Generate and persist a meeting summary. Returns the new row id.

    Default (no lens): the business-meeting PROMPT_TEMPLATE. With `lens_path`:
    the lens file dictates priming + objective + output contract; the engine
    carries no per-purpose logic (swap the lens, change the output). One happy
    path, parameterized — not a fork.
    """
    chunk_rows = _load_chunks(call_id)
    transcript = _format_transcript(chunk_rows)
    lens_name = Path(lens_path).name if lens_path else None

    def _build(t: str) -> str:
        return _build_lens_prompt(lens_path, t) if lens_path else PROMPT_TEMPLATE.format(transcript=t)

    if phi:
        from .scrub import rehydrate, scrub
        scrubbed_transcript, mapping = scrub(transcript)
        content, model_used = complete_with_fallback(_build(scrubbed_transcript), max_tokens=max_tokens)
        content = rehydrate(content, mapping)
    else:
        content, model_used = complete_with_fallback(_build(transcript), max_tokens=max_tokens)

    return _persist_summary(
        call_id=call_id,
        content=content,
        model_used=model_used,
        phi_scrubbed=phi,
        lens=lens_name,
    )


def upsert_outline(call_id: int, content: str) -> int:
    """Insert or update the outline for a call. Returns the outline id."""
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO outlines (call_id, content) VALUES (%s, %s) "
                "ON CONFLICT (call_id) DO UPDATE "
                "SET content = EXCLUDED.content, updated_at = now() "
                "RETURNING id",
                (call_id, content),
            )
            outline_id = cur.fetchone()[0]
            conn.commit()
    return outline_id


def get_outline(call_id: int) -> str | None:
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT content FROM outlines WHERE call_id = %s", (call_id,))
            row = cur.fetchone()
            return row[0] if row else None


def update_summary_content(summary_id: int, content: str) -> None:
    """Replace the markdown content of an existing summary row in place."""
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE meeting_summaries SET content = %s WHERE id = %s",
                (content, summary_id),
            )
            conn.commit()


def get_summary(call_id: int, summary_id: int | None = None) -> dict | None:
    """Return most recent (or specified) summary as a dict."""
    with psycopg.connect(DB_URL, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            if summary_id is not None:
                cur.execute(
                    "SELECT id, call_id, content, model_used, phi_scrubbed, lens, created_at "
                    "FROM meeting_summaries WHERE id = %s",
                    (summary_id,),
                )
            else:
                cur.execute(
                    "SELECT id, call_id, content, model_used, phi_scrubbed, lens, created_at "
                    "FROM meeting_summaries WHERE call_id = %s "
                    "ORDER BY created_at DESC LIMIT 1",
                    (call_id,),
                )
            row = cur.fetchone()
            return dict(row) if row else None
