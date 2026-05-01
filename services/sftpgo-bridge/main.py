"""SFTPGo → OpenWebUI bridge.

Receives SFTPGo event-action webhooks and mirrors each user's file changes into
their OpenWebUI knowledge collection.

Flow per upload:
  1. SFTPGo POSTs an event payload to /events
  2. We extract username (email) and the relative path inside their bucket prefix
  3. Find or create a per-user OpenWebUI knowledge collection ("OXP Files")
  4. Download the object from the Railway bucket
  5. POST to OpenWebUI's /api/v1/files/, then poll status until "completed"
  6. POST /api/v1/knowledge/{id}/file/add to attach the file to the collection
  7. Record (user, s3_key, ow_file_id, ow_collection_id, action_at) in bridge_sync_log

For deletes: look up the file_id in the sync log, remove from collection, delete the file.
For renames: treat as delete + upload (SFTPGo emits both for us).

The bridge is server-to-server only — it accepts a shared HMAC token from SFTPGo so the
endpoint can't be called from anywhere else even though it's on a public Railway URL.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
import boto3
import httpx
from fastapi import FastAPI, Header, HTTPException, Request

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bridge")


# ── Config (fail-fast) ────────────────────────────────────────────────────────

DATABASE_URL = os.environ["DATABASE_URL"]
WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]
S3_BUCKET = os.environ["S3_BUCKET"]
S3_ENDPOINT = os.environ["S3_ENDPOINT"]
S3_REGION = os.environ.get("S3_REGION", "auto")
S3_ACCESS_KEY = os.environ["S3_ACCESS_KEY"]
S3_SECRET_KEY = os.environ["S3_SECRET_KEY"]
S3_USER_PREFIX = os.environ.get("S3_USER_PREFIX", "users/")
OPENWEBUI_BASE_URL = os.environ["OPENWEBUI_BASE_URL"].rstrip("/")
OPENWEBUI_API_KEY = os.environ["OPENWEBUI_API_KEY"]
COLLECTION_NAME = os.environ.get("COLLECTION_NAME", "OXP Files")
ALLOWED_EMAIL_DOMAINS = tuple(
    d.strip().lower()
    for d in os.environ.get(
        "ALLOWED_EMAIL_DOMAINS",
        "orthoxpress.com,strongprompt.ai,bilberryindustries.com",
    ).split(",")
    if d.strip()
)
PROCESS_POLL_INTERVAL_S = float(os.environ.get("PROCESS_POLL_INTERVAL_S", "2"))
PROCESS_POLL_TIMEOUT_S = float(os.environ.get("PROCESS_POLL_TIMEOUT_S", "300"))


# ── Schema ────────────────────────────────────────────────────────────────────

CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS bridge_sync_log (
    id              BIGSERIAL PRIMARY KEY,
    user_email      TEXT NOT NULL,
    s3_key          TEXT NOT NULL,
    ow_file_id      TEXT,
    ow_collection_id TEXT,
    action          TEXT NOT NULL,
    status          TEXT NOT NULL,
    detail          TEXT,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_bridge_sync_log_active
    ON bridge_sync_log(user_email, s3_key)
    WHERE status = 'synced';
CREATE INDEX IF NOT EXISTS idx_bridge_sync_log_user
    ON bridge_sync_log(user_email);

CREATE TABLE IF NOT EXISTS bridge_user_collections (
    user_email      TEXT PRIMARY KEY,
    ow_collection_id TEXT NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT now()
);
"""


# ── Lifespan ──────────────────────────────────────────────────────────────────

pool: asyncpg.Pool = None  # type: ignore[assignment]
s3: Any = None
ow: httpx.AsyncClient = None  # type: ignore[assignment]


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool, s3, ow
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=4)
    async with pool.acquire() as conn:
        await conn.execute(CREATE_TABLES)
    s3 = boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        region_name=S3_REGION,
    )
    ow = httpx.AsyncClient(
        base_url=OPENWEBUI_BASE_URL,
        headers={"Authorization": f"Bearer {OPENWEBUI_API_KEY}"},
        timeout=httpx.Timeout(60.0, connect=10.0),
    )
    log.info("bridge ready: bucket=%s ow=%s", S3_BUCKET, OPENWEBUI_BASE_URL)
    yield
    await pool.close()
    await ow.aclose()


app = FastAPI(lifespan=lifespan)


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"service": "sftpgo-bridge"}


# ── Auth helper ───────────────────────────────────────────────────────────────

def _verify_secret(provided: str | None) -> None:
    """Constant-time compare of the shared secret SFTPGo sends in a header."""
    if not provided or not hmac.compare_digest(provided, WEBHOOK_SECRET):
        raise HTTPException(status_code=401, detail="bad shared secret")


# ── OpenWebUI helpers ─────────────────────────────────────────────────────────

async def _ow_find_user_id(email: str) -> str | None:
    r = await ow.get("/api/v1/users/", params={"query": email})
    if r.status_code != 200:
        log.warning("ow user lookup %s -> %s %s", email, r.status_code, r.text[:200])
        return None
    body = r.json()
    users = body.get("users") if isinstance(body, dict) else body
    if not users:
        return None
    for u in users:
        if (u.get("email") or "").lower() == email.lower():
            return u.get("id")
    return None


async def _ow_get_or_create_collection(user_email: str) -> str:
    """Find or create the per-user OXP Files knowledge collection.

    Idempotent. Caches the collection_id in bridge_user_collections so we don't
    hit OpenWebUI's list endpoint on every event.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT ow_collection_id FROM bridge_user_collections WHERE user_email=$1",
            user_email,
        )
        if row:
            return row["ow_collection_id"]

    user_id = await _ow_find_user_id(user_email)

    name = f"{COLLECTION_NAME} · {user_email}"
    payload: dict = {
        "name": name,
        "description": f"Files synced from oxp.files for {user_email}",
        "data": {},
    }
    if user_id:
        payload["access_control"] = {
            "read": {"user_ids": [user_id], "group_ids": []},
            "write": {"user_ids": [user_id], "group_ids": []},
        }
    r = await ow.post("/api/v1/knowledge/create", json=payload)
    if r.status_code != 200:
        log.error("ow knowledge create failed %s: %s", r.status_code, r.text[:300])
        raise HTTPException(502, "openwebui knowledge create failed")
    cid = r.json()["id"]

    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO bridge_user_collections (user_email, ow_collection_id) VALUES ($1, $2) "
            "ON CONFLICT (user_email) DO UPDATE SET ow_collection_id = EXCLUDED.ow_collection_id",
            user_email, cid,
        )
    log.info("created collection for %s -> %s (user_id=%s)", user_email, cid, user_id)
    return cid


async def _ow_upload_file(filename: str, content: bytes, content_type: str) -> str:
    files = {"file": (filename, io.BytesIO(content), content_type)}
    r = await ow.post("/api/v1/files/", files=files)
    if r.status_code != 200:
        log.error("ow file upload failed %s: %s", r.status_code, r.text[:300])
        raise HTTPException(502, "openwebui file upload failed")
    return r.json()["id"]


async def _ow_wait_for_processing(file_id: str) -> None:
    deadline = time.time() + PROCESS_POLL_TIMEOUT_S
    while time.time() < deadline:
        r = await ow.get(f"/api/v1/files/{file_id}/process/status")
        if r.status_code == 200:
            status = (r.json() or {}).get("status")
            if status == "completed":
                return
            if status == "failed":
                raise HTTPException(502, f"openwebui processing failed for {file_id}")
        await asyncio.sleep(PROCESS_POLL_INTERVAL_S)
    raise HTTPException(504, f"openwebui processing timeout for {file_id}")


async def _ow_attach_file_to_collection(collection_id: str, file_id: str) -> None:
    r = await ow.post(
        f"/api/v1/knowledge/{collection_id}/file/add",
        json={"file_id": file_id},
    )
    if r.status_code != 200:
        log.error("ow attach failed %s: %s", r.status_code, r.text[:300])
        raise HTTPException(502, "openwebui knowledge attach failed")


async def _ow_detach_file_from_collection(collection_id: str, file_id: str) -> None:
    r = await ow.post(
        f"/api/v1/knowledge/{collection_id}/file/remove",
        json={"file_id": file_id},
    )
    if r.status_code != 200:
        log.warning("ow detach %s/%s -> %s %s",
                    collection_id, file_id, r.status_code, r.text[:200])


async def _ow_delete_file(file_id: str) -> None:
    r = await ow.delete(f"/api/v1/files/{file_id}")
    if r.status_code not in (200, 404):
        log.warning("ow delete %s -> %s %s", file_id, r.status_code, r.text[:200])


# ── S3 helpers ────────────────────────────────────────────────────────────────

def _s3_key_for(user_email: str, virtual_path: str) -> str:
    """SFTPGo's virtual path is something like '/notes.pdf' — strip leading '/'
    and prepend the user's S3 prefix per their group's filesystem template."""
    rel = virtual_path.lstrip("/")
    return f"{S3_USER_PREFIX}{user_email}/{rel}"


def _s3_get(key: str) -> tuple[bytes, str]:
    obj = s3.get_object(Bucket=S3_BUCKET, Key=key)
    body = obj["Body"].read()
    ctype = obj.get("ContentType") or "application/octet-stream"
    return body, ctype


# ── Sync log helpers ──────────────────────────────────────────────────────────

async def _log_action(
    user_email: str, s3_key: str, action: str, status: str,
    ow_file_id: str = "", ow_collection_id: str = "", detail: str = "",
):
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO bridge_sync_log
               (user_email, s3_key, action, status, ow_file_id, ow_collection_id, detail)
               VALUES ($1,$2,$3,$4,$5,$6,$7)""",
            user_email, s3_key, action, status, ow_file_id, ow_collection_id, detail,
        )


async def _find_synced(user_email: str, s3_key: str) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT ow_file_id, ow_collection_id FROM bridge_sync_log
               WHERE user_email=$1 AND s3_key=$2 AND status='synced'
               ORDER BY id DESC LIMIT 1""",
            user_email, s3_key,
        )
    return dict(row) if row else None


# ── Action handlers ───────────────────────────────────────────────────────────

async def _handle_upload(user_email: str, virtual_path: str) -> str:
    s3_key = _s3_key_for(user_email, virtual_path)
    filename = virtual_path.rsplit("/", 1)[-1]

    # If we previously synced this exact key, detach the old version first so
    # we don't end up with duplicates (handles overwrites cleanly).
    prev = await _find_synced(user_email, s3_key)
    if prev:
        await _ow_detach_file_from_collection(prev["ow_collection_id"], prev["ow_file_id"])
        await _ow_delete_file(prev["ow_file_id"])
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE bridge_sync_log SET status='superseded', updated_at=now() "
                "WHERE user_email=$1 AND s3_key=$2 AND status='synced'",
                user_email, s3_key,
            )

    content, content_type = _s3_get(s3_key)
    file_id = await _ow_upload_file(filename, content, content_type)
    await _ow_wait_for_processing(file_id)
    collection_id = await _ow_get_or_create_collection(user_email)
    await _ow_attach_file_to_collection(collection_id, file_id)
    await _log_action(user_email, s3_key, "upload", "synced",
                      ow_file_id=file_id, ow_collection_id=collection_id)
    return file_id


async def _handle_delete(user_email: str, virtual_path: str) -> None:
    s3_key = _s3_key_for(user_email, virtual_path)
    prev = await _find_synced(user_email, s3_key)
    if not prev:
        log.info("delete: no synced record for %s, ignoring", s3_key)
        return
    await _ow_detach_file_from_collection(prev["ow_collection_id"], prev["ow_file_id"])
    await _ow_delete_file(prev["ow_file_id"])
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE bridge_sync_log SET status='deleted', updated_at=now() "
            "WHERE user_email=$1 AND s3_key=$2 AND status='synced'",
            user_email, s3_key,
        )
    await _log_action(user_email, s3_key, "delete", "deleted",
                      ow_file_id=prev["ow_file_id"],
                      ow_collection_id=prev["ow_collection_id"])


async def _handle_rename(user_email: str, old_path: str, new_path: str) -> None:
    await _handle_delete(user_email, old_path)
    await _handle_upload(user_email, new_path)


# ── Webhook endpoint ──────────────────────────────────────────────────────────

@app.post("/events")
async def receive_event(
    request: Request,
    x_bridge_secret: str | None = Header(default=None, alias="X-Bridge-Secret"),
):
    _verify_secret(x_bridge_secret)
    body = await request.json()
    log.info("event received: %s", {k: v for k, v in body.items() if k != "payload"})

    # SFTPGo's event payload field names per their docs
    action = (body.get("action") or body.get("event") or "").lower()
    user_email = (body.get("username") or "").strip().lower()
    virtual_path = body.get("virtual_path") or body.get("path") or ""
    target_path = body.get("virtual_target_path") or body.get("target_path") or ""

    if "@" not in user_email:
        log.info("ignoring non-email user: %r", user_email)
        return {"status": "ignored", "reason": "non-email username"}

    domain = user_email.rsplit("@", 1)[-1]
    if domain not in ALLOWED_EMAIL_DOMAINS:
        log.info("ignoring email outside whitelist: %r", user_email)
        return {"status": "ignored", "reason": "domain not whitelisted"}

    try:
        if action in ("upload", "first-upload", "first_upload"):
            file_id = await _handle_upload(user_email, virtual_path)
            return {"status": "synced", "ow_file_id": file_id}
        elif action in ("delete", "rmdir"):
            await _handle_delete(user_email, virtual_path)
            return {"status": "deleted"}
        elif action in ("rename", "renamed"):
            await _handle_rename(user_email, virtual_path, target_path)
            return {"status": "renamed"}
        else:
            log.info("ignoring action %r", action)
            return {"status": "ignored", "reason": f"action {action} not handled"}
    except HTTPException:
        raise
    except Exception as e:
        log.exception("event handler failed")
        await _log_action(user_email, _s3_key_for(user_email, virtual_path),
                          action or "?", "error", detail=str(e)[:500])
        raise HTTPException(500, f"bridge error: {e}")
