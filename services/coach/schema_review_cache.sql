-- coach_review_cache — coach-owned live public-review cache for mine_practice_reviews.
--
-- Keyed by a normalized practice+city key (review_cache.cache_key — lowercase, strip
-- punctuation, drop honorifics/practice-type noise; NEVER reduced to surname). Holds the
-- full fetch_review_insights() return so a same-practice follow-up within the TTL reuses
-- it instead of re-running the ~40s/$0.10 live fetch.
--
-- Coach-owned (NOT roadmap.review_cache): the roadmap chat is being retired, so sharing
-- buys nothing. Same shape, in the coach's public schema alongside coach_allowlist/coach_floor.
--
-- cache_key is LLM-free-text-derived, so it is length-bounded by a CHECK. Point-lookup only
-- (PK): TTL is checked read-side (EXTRACT(EPOCH FROM now()-fetched_at) < ttl), never by a
-- batch scan, so there is deliberately NO index on fetched_at and no index beyond the PK.
--
-- Only SUCCESSFUL, non-empty fetches are written (review_cache._is_cacheable) — a transient
-- outage must never pin an empty block for a whole TTL.
CREATE TABLE IF NOT EXISTS coach_review_cache (
    cache_key    text        PRIMARY KEY CHECK (length(cache_key) BETWEEN 1 AND 500),
    practice_raw text,
    city_raw     text,
    insights     jsonb       NOT NULL,
    fetched_at   timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE coach_review_cache IS
    'Coach live public-review cache for mine_practice_reviews. cache_key = normalized practice+city (review_cache.cache_key, applied identically read+write). insights = full fetch_review_insights() return (jsonb). TTL enforced read-side via COACH_REVIEW_CACHE_TTL_SECONDS (default 7d); rows evicted by a scheduled DELETE, never on read. Only successful, non-empty fetches stored.';

-- Eviction (DEFERRED — file it now). Past-TTL rows are never read but never removed. Run
-- DAILY, OFF the request path (pg_cron / Railway scheduled job). 30d ≈ 4× the 7d TTL.
-- NEVER evict on read (it would turn a point lookup into a write transaction).
--     DELETE FROM coach_review_cache WHERE fetched_at < now() - interval '30 days';
