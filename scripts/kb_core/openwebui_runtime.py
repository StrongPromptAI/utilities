"""Live-state inspection for the OpenWebUI Postgres in the oxp-kb project.

Distinct from the kb project's own Postgres (that one holds call transcripts +
openwebui-docs chunks). This module is for peeking at OpenWebUI's *runtime*
state: users signed up, knowledge bases uploaded, admin config saved, etc.

Access goes via the Railway public TCP proxy on the oxp-kb postgres service.
All queries are wrapped in a READ ONLY transaction as a safety belt — this
module intentionally has no write path.
"""
import json
import os
import subprocess
from typing import Optional

import psycopg
from psycopg.rows import dict_row


_OXP_KB_PROJECT_ID = "96a6d9dd-b680-4821-bee6-ed850a19074b"
_OXP_KB_ENV_ID = "30bf77ef-ec92-472d-b92a-93e3806bd7e4"
_OXP_KB_POSTGRES_SERVICE_ID = "6e3ccd17-d0fd-42c5-8edd-bef785445c57"

_CACHED_URL: Optional[str] = None


def _gql(query: str) -> dict:
    """POST a GraphQL query with the Railway token; returns data."""
    token = json.load(open(os.path.expanduser("~/.config/keys.json")))["railway_main"]
    result = subprocess.run(
        ["curl", "-s", "-X", "POST", "https://backboard.railway.com/graphql/v2",
         "-H", f"Authorization: Bearer {token}",
         "-H", "Content-Type: application/json",
         "-d", json.dumps({"query": query})],
        capture_output=True, text=True, timeout=10,
    )
    return json.loads(result.stdout)["data"]


def _get_db_url() -> str:
    """Resolve the public Postgres URL for oxp-kb. Cached per process.

    Composes the URL from (a) the TCP proxy domain+port and (b) the POSTGRES_*
    env vars on the service. Railway only auto-populates DATABASE_PUBLIC_URL
    when the proxy is created via dashboard, not via GraphQL — so we build
    it ourselves.

    Override with OXP_KB_DATABASE_URL for local testing.
    """
    global _CACHED_URL
    if _CACHED_URL:
        return _CACHED_URL
    if override := os.environ.get("OXP_KB_DATABASE_URL"):
        _CACHED_URL = override
        return _CACHED_URL

    vars_data = _gql(
        f'query {{ variables(projectId: "{_OXP_KB_PROJECT_ID}", '
        f'environmentId: "{_OXP_KB_ENV_ID}", '
        f'serviceId: "{_OXP_KB_POSTGRES_SERVICE_ID}") }}'
    )["variables"]

    # Prefer the Railway-provided URL if it's set (e.g. proxy was enabled via UI)
    if url := vars_data.get("DATABASE_PUBLIC_URL"):
        _CACHED_URL = url
        return _CACHED_URL

    proxies = _gql(
        f'query {{ tcpProxies(serviceId: "{_OXP_KB_POSTGRES_SERVICE_ID}", '
        f'environmentId: "{_OXP_KB_ENV_ID}") {{ domain proxyPort applicationPort }} }}'
    )["tcpProxies"]
    if not proxies:
        raise RuntimeError(
            "No TCP proxy on oxp-kb postgres. Enable one via Railway dashboard "
            "(postgres → Settings → TCP Proxy) or with the tcpProxyCreate mutation."
        )
    pg = proxies[0]
    host = pg["domain"].rstrip(".")  # Railway sometimes returns trailing dot
    port = pg["proxyPort"]
    user = vars_data["POSTGRES_USER"]
    password = vars_data["POSTGRES_PASSWORD"]
    db = vars_data.get("POSTGRES_DB", "postgres")
    _CACHED_URL = f"postgresql://{user}:{password}@{host}:{port}/{db}"
    return _CACHED_URL


def _connect():
    """Connect with READ ONLY transaction mode as a safety belt."""
    conn = psycopg.connect(_get_db_url(), row_factory=dict_row, connect_timeout=10)
    conn.execute("SET default_transaction_read_only = on")
    return conn


# ─── Inspection functions ────────────────────────────────────────────────────

def list_users(limit: int = 20) -> list[dict]:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT u.id, u.name, u.email, u.role,
                       to_timestamp(u.created_at) AS created_at,
                       to_timestamp(u.last_active_at) AS last_active_at,
                       a.active AS auth_active
                FROM "user" u
                LEFT JOIN auth a ON a.id = u.id
                ORDER BY u.created_at DESC
                LIMIT %s
            """, (limit,))
            return cur.fetchall()


def get_config() -> dict:
    """Return OpenWebUI's config singleton (what admin UI actually saved)."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, version, data, updated_at FROM config WHERE id=1")
            row = cur.fetchone()
            if not row:
                return {}
            return {
                "id": row["id"],
                "version": row["version"],
                "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
                "data": row["data"],
            }


def list_knowledge(limit: int = 20) -> list[dict]:
    """Knowledge bases uploaded via the UI (separate from admin-panel RAG config)."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT k.id, k.name, k.description, k.user_id,
                       to_timestamp(k.created_at) AS created_at,
                       (SELECT COUNT(*) FROM knowledge_file kf WHERE kf.knowledge_id = k.id) AS file_count
                FROM knowledge k
                ORDER BY k.created_at DESC
                LIMIT %s
            """, (limit,))
            return cur.fetchall()


def list_files(limit: int = 30) -> list[dict]:
    """Uploaded files (standalone or attached to a knowledge base)."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, filename, user_id, path,
                       to_timestamp(created_at) AS created_at
                FROM file
                ORDER BY created_at DESC
                LIMIT %s
            """, (limit,))
            return cur.fetchall()


def list_chats(limit: int = 20) -> list[dict]:
    """Chat METADATA only (no message bodies). Respect user privacy."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT c.id, c.title, c.user_id, c.archived, c.pinned,
                       to_timestamp(c.created_at) AS created_at,
                       to_timestamp(c.updated_at) AS updated_at,
                       jsonb_array_length(COALESCE((c.chat::jsonb -> 'messages'), '[]'::jsonb)) AS message_count
                FROM chat c
                ORDER BY c.updated_at DESC
                LIMIT %s
            """, (limit,))
            return cur.fetchall()


def list_models(limit: int = 30, active_only: bool = True) -> list[dict]:
    """Models configured in OpenWebUI (from model table — admin-defined variants)."""
    with _connect() as conn:
        with conn.cursor() as cur:
            where = "WHERE is_active = true" if active_only else ""
            cur.execute(f"""
                SELECT id, name, base_model_id, is_active,
                       to_timestamp(created_at) AS created_at
                FROM model
                {where}
                ORDER BY created_at DESC
                LIMIT %s
            """, (limit,))
            return cur.fetchall()


def count_all() -> dict:
    """One-shot stats summary."""
    with _connect() as conn:
        with conn.cursor() as cur:
            out = {}
            for tbl in ["user", "auth", "chat", "chat_message", "knowledge",
                        "knowledge_file", "file", "model", "memory", "folder"]:
                try:
                    cur.execute(f'SELECT COUNT(*) AS n FROM "{tbl}"')
                    out[tbl] = cur.fetchone()["n"]
                except psycopg.Error:
                    out[tbl] = None
            return out


def get_config_key(dotted_key: str):
    """Dig into the config JSON by dotted path, e.g. 'rag.web.search.engine'.
    Returns None if the path is absent."""
    cfg = get_config().get("data") or {}
    node = cfg
    for part in dotted_key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node
