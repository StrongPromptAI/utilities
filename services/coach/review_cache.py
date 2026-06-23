"""review_cache — coach-owned cache + rendering for the mine_practice_reviews tool.

This is the DB-aware half of meeting prep. The pure engine (`meeting_prep.py`) mines
public reviews into cited FACTS; this module:
  • caches a successful fetch by a normalized practice+city key (coach_review_cache),
    TTL-gated and fail-OPEN, so a same-practice follow-up reuses it (the fetch is slow,
    ~40s/$0.10);
  • renders the facts into a tool_result block the agentic model (GLM) synthesizes the
    reputation READ + DME OPENER from — GLM does the synthesis in-loop (one happy path),
    so the block carries the facts AND a short DME-economics framing instruction;
  • drives the slow fetch as a streaming orchestrator (`mine_streaming`) that emits
    PROGRESS events while it works ("get credit for the homework") and finally the result.

Kept separate from the engine on purpose: the engine stays self-contained (no DB), this
module owns the coach's store. Cache table is coach-owned (coach_review_cache) — NOT
roadmap.review_cache; the roadmap chat is being retired, so sharing buys nothing.

Never raises out of `mine_streaming` — a fetch/DB failure degrades to an honest
"couldn't pull live reviews" block. Cite-only-real-quotes integrity is the engine's job
(citation-validation); this module never fabricates a rating, review, or URL.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row

import meeting_prep

# Cache freshness window. Constant + env override (the coach has no settings table yet —
# promote to a coach_settings row if/when one lands). 7 days amortizes the slow fetch
# across reps prepping the same practice; the block always discloses the real age.
CACHE_TTL_SECONDS = int(os.environ.get("COACH_REVIEW_CACHE_TTL_SECONDS", "604800"))

# Fail-open hardening: a missing/slow DB must degrade to a live fetch, not hang the loop.
_CACHE_CONNECT_TIMEOUT = 3          # seconds
_CACHE_STATEMENT_TIMEOUT_MS = 3000  # 3s

# Per-process locks keyed by cache_key — coalesce concurrent same-key misses on one worker
# so a double-call doesn't spend ~40s/$0.10 twice. Cross-worker duplication is accepted.
_CACHE_LOCKS: dict[str, asyncio.Lock] = {}


# ── cache key normalization (identical on read + write) ─────────────────────
# Honorific/title + practice-type-noise tokens dropped so "Dr. John Andrawis",
# "john andrawis md", and "John Andrawis Orthopedics" collapse to one key. We deliberately
# do NOT reduce to surname: a miss + re-fetch for "Dr. Andrawis" vs "Dr. John Andrawis" is
# acceptable; serving the WRONG same-surname doctor's reviews is not (the URLs are real,
# just for the wrong doctor — the citation check can't catch that).
_KEY_STOPWORDS = frozenset({
    "dr", "doctor", "md", "do",                      # honorifics / degree suffixes
    "orthopedics", "orthopedic", "ortho",            # practice-type noise
    "inc", "llc", "pa", "pc",                         # corporate suffixes
})
_KEY_JOIN = re.compile(r"[.'’]")            # abbreviation dots/apostrophes removed (join)
_KEY_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)  # other punctuation → space (separate)


def _norm(s: str | None) -> str:
    if not s:
        return ""
    folded = _KEY_JOIN.sub("", s.lower())       # "m.d." -> "md", "mary's" -> "marys"
    cleaned = _KEY_PUNCT.sub(" ", folded)       # commas / hyphens / slashes -> space
    return " ".join(t for t in cleaned.split() if t and t not in _KEY_STOPWORDS)


def cache_key(practice: str | None, city: str | None) -> str:
    """Pure, deterministic cache key. Normalized (case/punct/honorific/type-noise) but
    NEVER reduced to surname. Empty city → '_' sentinel. Applied IDENTICALLY read + write."""
    return f"{_norm(practice)}|{_norm(city) or '_'}"


# ── Postgres cache ops (short-timeout, fail-open) ───────────────────────────

def _db_url() -> str:
    url = os.environ.get("COACH_DB_URL") or os.environ.get("KB_DATABASE_URL")
    if not url:
        raise RuntimeError("FAIL-FAST: COACH_DB_URL (or KB_DATABASE_URL) not set for review cache")
    return url


def _cache_connect():
    """Short-timeout connection so 'fail open' is real: neither a dead network nor a slow
    query can hang the wrapper for more than a few seconds."""
    return psycopg.connect(
        _db_url(),
        connect_timeout=_CACHE_CONNECT_TIMEOUT,
        options=f"-c statement_timeout={_CACHE_STATEMENT_TIMEOUT_MS}",
        row_factory=dict_row,
    )


def _cache_get_sync(key: str, ttl_seconds: int) -> tuple[dict, datetime] | None:
    """SELECT a fresh cache row (within TTL). Returns (insights, fetched_at) or None on a
    miss. Raises on any DB error — the async caller treats a raise as a miss (fail open)."""
    with _cache_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT insights, fetched_at
                FROM coach_review_cache
                WHERE cache_key = %(k)s
                  AND EXTRACT(EPOCH FROM now() - fetched_at) < COALESCE(%(ttl)s, 604800)
                """,
                {"k": key, "ttl": ttl_seconds},
            )
            row = cur.fetchone()
    if not row:
        return None
    return row["insights"], row["fetched_at"]


def _cache_put_sync(key: str, practice_raw: str | None, city_raw: str | None, insights_json: str) -> None:
    """UPSERT one cache row. insights is PRE-SERIALIZED (json.dumps(default=str)) and cast
    ::jsonb here. Last-writer-wins on conflict — both racing fetches are validly fresh."""
    with _cache_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO coach_review_cache (cache_key, practice_raw, city_raw, insights, fetched_at)
                VALUES (%(k)s, %(p)s, %(c)s, %(j)s::jsonb, now())
                ON CONFLICT (cache_key) DO UPDATE SET
                    insights     = EXCLUDED.insights,
                    practice_raw = EXCLUDED.practice_raw,
                    city_raw     = EXCLUDED.city_raw,
                    fetched_at   = now()
                """,
                {"k": key, "p": practice_raw, "c": city_raw, "j": insights_json},
            )


def _is_cacheable(insights: dict) -> bool:
    """Only SUCCESSFUL, NON-EMPTY fetches are cached. A zero-signal AND null-rating result
    (a transient outage that 'succeeded' with nothing) must never pin an empty block for a
    whole TTL — that would be a silent failure served as fresh."""
    return bool(insights.get("signals")) or bool(insights.get("rating"))


def _cache_write(key: str, practice_raw: str | None, city_raw: str | None, insights: dict) -> None:
    """Fire-and-forget UPSERT body (daemon thread). Swallows ALL errors: a write hiccup must
    never poison an already-successful fetch."""
    if not (1 <= len(key) <= 500):  # mirror the PK CHECK — never error on a pathological key
        print(f"[coach] review cache key length {len(key)} out of bounds — not stored")
        return
    try:
        payload = json.dumps(insights, default=str)
    except Exception as exc:  # noqa: BLE001
        print(f"[coach] review cache encode failed (non-fatal): {exc!r}")
        return
    try:
        _cache_put_sync(key, practice_raw, city_raw, payload)
    except Exception as exc:  # noqa: BLE001
        print(f"[coach] review cache write failed (non-fatal): {exc!r}")


def _spawn_cache_write(key: str, practice_raw: str | None, city_raw: str | None, insights: dict) -> None:
    threading.Thread(target=_cache_write, args=(key, practice_raw, city_raw, insights), daemon=True).start()


async def cached_review_insights(practice: str, city: str | None) -> tuple[dict, datetime | None, bool]:
    """Cache wrapper around meeting_prep.fetch_review_insights, keyed on a normalized
    practice+city key and TTL-gated.

    Returns (insights, fetched_at, was_cached):
      • HIT  → cached insights, the row's real fetched_at, True   (NO live fetch)
      • MISS → fresh insights, None (= 'mined just now'), False   (live fetch + bg UPSERT)

    Fail OPEN: any cache/DB error falls through to a live fetch — this wrapper never raises
    on a cache fault. The live fetch ITSELF may still raise (transient outage); that
    propagates to the caller, which renders the 'unavailable' block."""
    key = cache_key(practice, city)
    lock = _CACHE_LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        try:
            cached = await asyncio.to_thread(_cache_get_sync, key, CACHE_TTL_SECONDS)
        except Exception as exc:  # noqa: BLE001
            print(f"[coach] review cache read failed (fail-open → live fetch): {exc!r}")
            cached = None
        if cached is not None:
            insights, fetched_at = cached
            print(f"[coach] review cache HIT key={key!r} fetched_at={fetched_at.isoformat()}")
            return insights, fetched_at, True

        print(f"[coach] review cache MISS key={key!r} → live fetch")
        insights = await meeting_prep.fetch_review_insights(practice, city)
        if _is_cacheable(insights):
            _spawn_cache_write(key, practice, city, insights)
        else:
            print(f"[coach] review result not cacheable (empty) key={key!r} — not stored")
        return insights, None, False


# ── tool_result rendering (facts + DME synthesis framing for GLM) ───────────

def _humanize_age(fetched_at: datetime) -> str:
    """Relative-age phrase for a cached fetch. fetched_at is tz-aware; compare against
    tz-aware UTC now. Clamped at 0 so clock skew never reads as the future."""
    secs = max(0, int((datetime.now(timezone.utc) - fetched_at).total_seconds()))
    if secs < 60:
        return "moments ago"
    mins = secs // 60
    if mins < 60:
        return f"~{mins} minute{'s' if mins != 1 else ''} ago"
    hours = secs // 3600
    if hours < 24:
        return f"~{hours} hour{'s' if hours != 1 else ''} ago"
    days = secs // 86400
    return "yesterday" if days == 1 else f"~{days} days ago"


# The synthesis instruction GLM reads alongside the facts. This is the MEETING_PREP behavior
# (carried from the roadmap chat) — but addressed to the agentic model as guidance for the
# turn it is about to write, not a separate Opus shot. DME-economics framing is the point.
_SYNTH_GUIDE = (
    "Now write the rep a tight READ then an OPENER, in plain conversational text.\n"
    "The READ answers ONE question: how much is this practice's reputation propped up by manual "
    "staff touchpoints, and how close is it to the volume ceiling where more cases erode the score? "
    "A high rating built on personal callbacks and hand-holding is a liability with a ceiling — to "
    "grow, the surgeon adds overhead or lets service slip and erodes the rating he fears losing. So "
    "name (a) how touchpoint-dependent the reputation looks (praise crediting personal attention ⇒ "
    "high), (b) whether communication/throughput cracks are already showing, (c) any incumbent app. "
    "ALWAYS ground the read in 1–2 SHORT VERBATIM patient quotes from the signals above (in quotation "
    "marks, light attribution like 'one Healthgrades review:') — the real words are what land with the "
    "surgeon. Then a 2–3 line OPENER running the frame: protect the score while unlocking throughput, "
    "grounded in surgeon economics — the ~70% phone-call/staff tax, post-op risk as the real margin "
    "under bundled payments, rating sensitivity. Cite ONLY the quotes/URLs above; never invent a "
    "rating, a review, or a URL. If the signals are thin, say so plainly rather than padding. Keep it "
    "tight, then offer to go deeper."
)


def build_facts_block(practice: str, city: str | None, insights: dict, fetched_at: datetime | None) -> str:
    """Render the tool_result: cited facts + the DME synthesis guide. fetched_at None →
    'mined just now'; a real timestamp → an honest relative-age phrase (serving a cached
    block as 'just now' would be a lie GLM repeats to the rep)."""
    practice = practice or insights.get("practice") or "the practice"
    city = city or insights.get("city")
    where = f" ({city})" if city else ""
    rating = insights.get("rating") or "not found"
    count = insights.get("review_count") or "not found"
    hg = insights.get("healthgrades_rating")
    hg_str = f" Healthgrades: {hg}." if hg else ""
    provenance = "mined just now from public sources only" if fetched_at is None \
        else f"from public sources, last mined {_humanize_age(fetched_at)}"
    sig_lines = [
        f'- [{s.get("theme")} · {s.get("sentiment")}] "{s.get("quote")}" — {s.get("source_url")}'
        for s in (insights.get("signals") or [])
    ]
    sig_block = "\n".join(sig_lines) if sig_lines else \
        "(no cited patient signals surfaced — say so plainly; do not invent any)"
    return (
        f"Public-review intelligence for {practice}{where}, {provenance}.\n"
        f"Google: {rating} stars · {count} reviews.{hg_str}\n"
        "Cited patient signals (theme · sentiment · verbatim quote · source URL):\n"
        f"{sig_block}\n\n"
        f"{_SYNTH_GUIDE}"
    )


def _unavailable_block(practice: str | None) -> str:
    practice = practice or "that practice"
    return (
        f"Live review lookup for {practice} could not be completed just now. Tell the rep you "
        "couldn't pull live reviews this moment, and offer to coach the meeting from the buyer "
        "profile instead (use search_value_prop for the surgeon/practice economics) — do not invent "
        "any reviews or ratings."
    )


def _need_name_block() -> str:
    return (
        "No specific practice or doctor name was provided, so no live reviews were pulled. Ask the "
        "rep which practice/surgeon and city they're meeting before prepping — do not guess a practice."
    )


# ── streaming orchestrator (progress events + final result) ─────────────────

async def mine_streaming(args: dict):
    """Async generator for the mine_practice_reviews tool. Yields progress events while the
    slow fetch runs, then exactly one result event. Never raises — a failure degrades to an
    honest 'unavailable' result the model relays.

    Yields dicts: {"type":"progress","text":...} (0+), then {"type":"result","text":...} (1).
    A cache HIT resolves before the first short probe, so NO scrape/reconcile step fires (a
    fast turn isn't padded with fake steps); the result follows immediately."""
    practice = (args or {}).get("practice", "").strip()
    city = ((args or {}).get("city") or "").strip() or None
    if not practice:
        yield {"type": "result", "text": _need_name_block()}
        return

    fetch_task = asyncio.create_task(cached_review_insights(practice, city))
    steps = [
        "Scraping public reviews (Google, Healthgrades, Yelp)…",
        "Reconciling the signals against the strain thesis…",
    ]
    step = -1
    interval = 1.5  # short first probe: a hit skips; a miss shows step 1 quickly
    while True:
        done, _ = await asyncio.wait({fetch_task}, timeout=interval)
        if done:
            break
        step = min(step + 1, len(steps) - 1)
        yield {"type": "progress", "text": steps[step]}
        interval = 7.0

    try:
        insights, fetched_at, was_cached = fetch_task.result()
    except Exception as exc:  # noqa: BLE001
        print(f"[coach] meeting_prep fetch failed (non-fatal): {exc!r}")
        yield {"type": "result", "text": _unavailable_block(practice)}
        return

    yield {"type": "progress", "text": "Crafting the reputation read and your opener…"}
    print(f"[coach] meeting_prep {practice!r} cached={was_cached} rating={insights.get('rating')} "
          f"signals={len(insights.get('signals') or [])} dropped={insights.get('dropped_signals')} "
          f"cost=${insights.get('cost_usd')}")
    yield {"type": "result", "text": build_facts_block(practice, city, insights, fetched_at)}
