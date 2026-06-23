"""Retrieval tools for the coach agent — vector search over the kb reference corpus.

The agent (agent.py) embeds the model's tool-query via shared-svcs embed, then calls
these; each returns the top-k chunks for the model to ground on. Embed-source-agnostic
on purpose: callers pass the query VECTOR, so the same code serves runtime (shared-svcs
nomic 768d) and tests (local ONNX) — both the same model + vector space as build_brain's
ingest.

Corpora (COACH_BRAIN §2 / §4 tool set):
  • search_value_prop → category 'thj_brain' (the audience-tagged THJ value-prop +
    stakeholder corpus). `audience` is returned so the caller MAY rank rep-facing >
    mixed; no boost is applied here (deferred lever — COACH_BRAIN §4).
  • search_method     → the two Peterson books (sales_framework = win-new,
    sales_expansion = grow-existing). DME-framing is the model's job, not retrieval's.
"""
from __future__ import annotations

# ── The coach's READ BOUNDARY (fail-closed allowlist) ──────────────────────
# The coach sees ONLY these reference_docs categories. NOT meeting/call transcripts
# (`call_chunks` — a different table, never queried here), NOT other books (Corporate
# Lifecycles = `sales_lifecycle`, The Science of Trust = `other`), NOT the roadmap
# chatbot's duplicate THJ ingest (`product_doctrine`/`sales_playbook`/`stakeholder_profile`).
# Allowlist, not denylist: a new category is invisible to the coach until added here.
VALUE_PROP_CATEGORY = "thj_brain"
METHOD_CATEGORIES = ("sales_framework", "sales_expansion")   # CTWTCS + The Expansion Sale
PODCAST_CATEGORY = "sales_podcast"                            # the team's Sales podcast (official feed pull)
# Deep policy/legal research (bundling, the 90-day global period, CA employed-physician
# side-practice/concierge rules). Reached ONLY via search_deep_research, which the model calls
# only when a rep explicitly digs into the specifics — kept OUT of thj_brain so a normal pitch
# question never pulls it (the intent gate, not a similarity threshold — COACH_BRAIN §4).
POLICY_RESEARCH_CATEGORY = "policy_research"
COACH_CATEGORIES = frozenset({VALUE_PROP_CATEGORY, *METHOD_CATEGORIES, PODCAST_CATEGORY, POLICY_RESEARCH_CATEGORY})

_SEARCH_SQL = """
SELECT rd.title, rd.audience, rd.category, dc.text,
       (1.0 - (dc.embedding <=> %(vec)s::vector)) AS score
FROM reference_doc_chunks dc
JOIN reference_docs rd ON dc.doc_id = rd.id
WHERE rd.category = ANY(%(cats)s)
  AND dc.embedding IS NOT NULL
ORDER BY dc.embedding <=> %(vec)s::vector
LIMIT %(k)s
"""


def _vec_literal(query_vec: list[float]) -> str:
    return "[" + ",".join(str(x) for x in query_vec) + "]"


def _search(conn, query_vec: list[float], categories: list[str], k: int) -> list[dict]:
    """Top-k chunks across `categories` by cosine similarity. `conn` is a dict-row
    psycopg connection (the service owns the pool); returns title/audience/category/text/score.

    Fail-closed: refuses any category outside the coach read boundary (COACH_CATEGORIES)."""
    outside = set(categories) - COACH_CATEGORIES
    if outside:
        raise ValueError(f"coach read-boundary violation: {sorted(outside)} not in allowlist {sorted(COACH_CATEGORIES)}")
    with conn.cursor() as cur:
        cur.execute(_SEARCH_SQL, {"vec": _vec_literal(query_vec), "cats": list(categories), "k": k})
        return cur.fetchall()


def search_value_prop(conn, query_vec: list[float], k: int = 6) -> list[dict]:
    """THJ value-prop + stakeholder material (audience-tagged corpus)."""
    return _search(conn, query_vec, [VALUE_PROP_CATEGORY], k)


def search_method(conn, query_vec: list[float], k: int = 6) -> list[dict]:
    """Sales method — both Peterson books (win-new + grow-existing)."""
    return _search(conn, query_vec, list(METHOD_CATEGORIES), k)


def search_podcast(conn, query_vec: list[float], k: int = 6) -> list[dict]:
    """The team's Sales podcast — living, voice-of-the-team selling content (incl. competitor takes)."""
    return _search(conn, query_vec, [PODCAST_CATEGORY], k)


def search_deep_research(conn, query_vec: list[float], k: int = 6) -> list[dict]:
    """Deep policy/legal research — bundling economics, the 90-day surgical global period, and
    California employed-physician side-practice/concierge rules. The rep's credibility backstop
    for a sophisticated follow-up; NOT pitch material."""
    return _search(conn, query_vec, [POLICY_RESEARCH_CATEGORY], k)
