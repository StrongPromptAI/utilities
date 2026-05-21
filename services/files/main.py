"""oxp.files — FastAPI app fronting a Tigris S3-compatible bucket.

Browser flow:
  /                            file browser (auth-gated, redirects to /oidc/login if no session)
  /?folder=<name>              same browser, scoped to one folder
  /oidc/login → /oidc/callback OAuth dance against oidc-otp
  /api/files[?folder=<name>]   list (JSON: {files, folders, current_folder})
  /api/files/upload-url        POST → presigned PUT URL (browser uploads direct to Tigris)
  /api/files/download/{name}   307 redirect to presigned GET URL
  /api/files/stream/{name}     307 redirect with inline disposition (for <audio>)
  /api/files/rename            POST → server-side rename within a folder
  /api/files/move              POST → server-side move across folders
  /api/files/{name}            DELETE
  /api/folders                 POST → create folder, body {name}
  /api/folders/{name}          DELETE (only if empty)
  /api/folders/rename          POST → rename folder + all child keys
  /logout                      clear local session + redirect to oidc-otp /logout

Storage: shared/ prefix with optional one-level folders (shared/<folder>/<file>).
Empty folders are persisted via a sentinel marker object so they survive listing.
Every authenticated user sees and can modify every other user's files
(intentional, small allowlist). Bucket versioning is enabled at startup so
deletes are recoverable for 30 days.
"""

from __future__ import annotations

import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

import boto3
import httpx
from botocore.exceptions import ClientError
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from jose import jwt
from jose.exceptions import JWTError
from pydantic import BaseModel

import templates

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("files")


# ── Config ────────────────────────────────────────────────────────────────────

SESSION_COOKIE_NAME = "oxp_files_session"
SESSION_COOKIE_TTL = 30 * 24 * 3600

STATE_COOKIE_NAME = "oxp_files_oauth_state"
STATE_COOKIE_TTL = 600

JWT_SECRET = os.environ["JWT_SECRET"]
OIDC_ISSUER = os.environ["OIDC_ISSUER"].rstrip("/")
OIDC_CLIENT_ID = os.environ["OIDC_CLIENT_ID"]
OIDC_CLIENT_SECRET = os.environ["OIDC_CLIENT_SECRET"]
OIDC_REDIRECT_URI = os.environ["OIDC_REDIRECT_URI"]
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "")

S3_BUCKET = os.environ["S3_BUCKET"]
S3_ENDPOINT = os.environ["S3_ENDPOINT"]
S3_ACCESS_KEY = os.environ["S3_ACCESS_KEY"]
S3_SECRET_KEY = os.environ["S3_SECRET_KEY"]
S3_REGION = os.environ.get("S3_REGION", "auto")
S3_PREFIX = os.environ.get("S3_PREFIX", "shared/")

ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get("ALLOWED_ORIGINS", PUBLIC_BASE_URL).split(",")
    if o.strip()
]

PRESIGN_EXPIRES = 3600  # 1 hour


# ── Lifespan ──────────────────────────────────────────────────────────────────

s3 = boto3.client(
    "s3",
    endpoint_url=S3_ENDPOINT,
    aws_access_key_id=S3_ACCESS_KEY,
    aws_secret_access_key=S3_SECRET_KEY,
    region_name=S3_REGION,
)

http_client: Optional[httpx.AsyncClient] = None
oidc_jwks: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client, oidc_jwks

    http_client = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0))

    # Pre-fetch JWKS so the first /oidc/callback doesn't pay the round-trip
    try:
        r = await http_client.get(f"{OIDC_ISSUER}/.well-known/jwks.json")
        if r.status_code == 200:
            oidc_jwks = r.json()
            log.info("loaded JWKS from %s", OIDC_ISSUER)
    except Exception as exc:
        log.warning("JWKS prefetch failed: %s — will retry on first verify", exc)

    # Bucket versioning — enables soft-delete recovery (Tigris retains old
    # versions; the lifecycle rule below garbage-collects them after 30 days).
    try:
        s3.put_bucket_versioning(
            Bucket=S3_BUCKET,
            VersioningConfiguration={"Status": "Enabled"},
        )
    except ClientError as exc:
        log.warning("put_bucket_versioning failed: %s", exc)

    # Lifecycle rule — expire noncurrent versions after 30 days so undelete
    # window stays bounded and storage cost doesn't grow unbounded.
    try:
        s3.put_bucket_lifecycle_configuration(
            Bucket=S3_BUCKET,
            LifecycleConfiguration={
                "Rules": [
                    {
                        "ID": "expire-noncurrent-30d",
                        "Status": "Enabled",
                        "Filter": {"Prefix": ""},
                        "NoncurrentVersionExpiration": {"NoncurrentDays": 30},
                        "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7},
                    }
                ]
            },
        )
    except ClientError as exc:
        log.warning("put_bucket_lifecycle_configuration failed: %s", exc)

    # CORS — required for browser direct PUT/GET to Tigris. Full replace, so
    # the app owns this config; do not hand-edit via Tigris console.
    if ALLOWED_ORIGINS:
        try:
            s3.put_bucket_cors(
                Bucket=S3_BUCKET,
                CORSConfiguration={
                    "CORSRules": [
                        {
                            "AllowedMethods": ["GET", "PUT", "DELETE", "HEAD"],
                            "AllowedOrigins": ALLOWED_ORIGINS,
                            "AllowedHeaders": ["*"],
                            "ExposeHeaders": ["ETag"],
                            "MaxAgeSeconds": 3600,
                        }
                    ]
                },
            )
            log.info("CORS configured for origins: %s", ALLOWED_ORIGINS)
        except ClientError as exc:
            log.warning("put_bucket_cors failed: %s", exc)

    log.info("oxp.files ready: bucket=%s prefix=%s", S3_BUCKET, S3_PREFIX)
    yield

    if http_client:
        await http_client.aclose()


app = FastAPI(lifespan=lifespan)


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/healthz")
async def healthz():
    return {"status": "ok", "service": "oxp-files"}


# ── Session & OIDC helpers ────────────────────────────────────────────────────

def _make_session_jwt(email: str) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": email,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=SESSION_COOKIE_TTL)).timestamp()),
        },
        JWT_SECRET,
        algorithm="HS256",
    )


def _verify_session_jwt(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return payload.get("sub")
    except (JWTError, KeyError):
        return None


def _set_session_cookie(response: Response, email: str) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        _make_session_jwt(email),
        max_age=SESSION_COOKIE_TTL,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


async def _get_jwks() -> dict:
    global oidc_jwks
    if not oidc_jwks:
        r = await http_client.get(f"{OIDC_ISSUER}/.well-known/jwks.json")
        oidc_jwks = r.json()
    return oidc_jwks


async def _verify_id_token(id_token: str) -> dict:
    jwks = await _get_jwks()
    return jwt.decode(
        id_token,
        jwks,
        algorithms=["RS256"],
        audience=OIDC_CLIENT_ID,
        issuer=OIDC_ISSUER,
    )


def require_user(request: Request) -> str:
    email = _verify_session_jwt(request.cookies.get(SESSION_COOKIE_NAME))
    if not email:
        raise HTTPException(status_code=401, detail="not authenticated")
    return email


# ── HTML routes ───────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home(request: Request, folder: str = ""):
    email = _verify_session_jwt(request.cookies.get(SESSION_COOKIE_NAME))
    if not email:
        return RedirectResponse("/oidc/login", status_code=303)

    folder = folder.strip()
    if folder:
        try:
            _safe_folder(folder)
        except HTTPException:
            return RedirectResponse("/", status_code=303)

    prefix = _prefix_for(folder or None)
    try:
        files, child_folders = _list_one_level(prefix)
    except ClientError as exc:
        log.exception("list_objects_v2 failed")
        return HTMLResponse(
            templates.error_html("OXP File Drop", f"Failed to list files: {exc}"),
            status_code=500,
        )

    # Inside a folder we never recurse, but we still need the full top-level
    # folder list so the "move to…" picker has somewhere to send things.
    if folder:
        try:
            _, all_folders = _list_one_level(S3_PREFIX)
        except ClientError:
            all_folders = []
    else:
        all_folders = child_folders

    return HTMLResponse(
        templates.file_browser_html(
            email=email,
            files=files,
            folders=all_folders,
            current_folder=folder,
        )
    )


# ── OIDC dance ────────────────────────────────────────────────────────────────

@app.get("/oidc/login")
async def oidc_login():
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(16)
    authorize_url = (
        f"{OIDC_ISSUER}/authorize"
        f"?client_id={OIDC_CLIENT_ID}"
        f"&response_type=code"
        f"&scope=openid+email+profile"
        f"&redirect_uri={OIDC_REDIRECT_URI}"
        f"&state={state}"
        f"&nonce={nonce}"
    )
    response = RedirectResponse(authorize_url, status_code=303)
    response.set_cookie(
        STATE_COOKIE_NAME,
        state,
        max_age=STATE_COOKIE_TTL,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    return response


@app.get("/oidc/callback")
async def oidc_callback(request: Request, code: str = "", state: str = ""):
    if not code or not state:
        raise HTTPException(400, "missing code or state")
    if request.cookies.get(STATE_COOKIE_NAME) != state:
        raise HTTPException(400, "state mismatch")

    r = await http_client.post(
        f"{OIDC_ISSUER}/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": OIDC_REDIRECT_URI,
            "client_id": OIDC_CLIENT_ID,
            "client_secret": OIDC_CLIENT_SECRET,
        },
    )
    if r.status_code != 200:
        log.warning("token exchange failed: %s %s", r.status_code, r.text[:300])
        raise HTTPException(502, "token exchange failed")

    id_token = r.json().get("id_token")
    if not id_token:
        raise HTTPException(502, "no id_token in response")

    try:
        claims = await _verify_id_token(id_token)
    except JWTError as exc:
        log.warning("id_token verify failed: %s", exc)
        raise HTTPException(401, "id_token invalid")

    email = claims.get("email")
    if not email:
        raise HTTPException(401, "id_token has no email")

    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(STATE_COOKIE_NAME, path="/")
    _set_session_cookie(response, email)
    log.info("logged in: %s***", email[:3])
    return response


@app.get("/logout")
async def logout():
    """Clear local session + bounce through IdP /logout to clear the SSO cookie too."""
    target = f"{OIDC_ISSUER}/logout"
    if PUBLIC_BASE_URL:
        target += f"?redirect_uri={PUBLIC_BASE_URL}/"
    response = RedirectResponse(target, status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return response


# ── File API ──────────────────────────────────────────────────────────────────

FOLDER_MARKER = ".keep"


def _safe_filename(name: str) -> str:
    """Reject path traversal and slashes — filenames are basename-only,
    the folder segment is passed separately."""
    if not name or "/" in name or "\\" in name or ".." in name or name.startswith("."):
        raise HTTPException(400, "invalid filename")
    if len(name) > 255:
        raise HTTPException(400, "filename too long")
    return name


def _safe_folder(name: str) -> str:
    """Validate a folder segment. One level only; no traversal, no slashes,
    no hidden names, no control chars, ≤64 chars."""
    if not name:
        raise HTTPException(400, "folder name required")
    if "/" in name or "\\" in name or ".." in name or name.startswith("."):
        raise HTTPException(400, "invalid folder name")
    if len(name) > 64:
        raise HTTPException(400, "folder name too long")
    if any(ord(c) < 32 for c in name):
        raise HTTPException(400, "folder name has control characters")
    return name


def _prefix_for(folder: Optional[str]) -> str:
    """Resolve the S3 prefix for an optional folder. Empty / None = root."""
    if not folder:
        return S3_PREFIX
    return f"{S3_PREFIX}{_safe_folder(folder)}/"


def _list_one_level(prefix: str) -> tuple[list[dict], list[str]]:
    """One-level list. Returns (files, child_folders).

    child_folders is meaningful only when prefix == S3_PREFIX (root view).
    The folder marker (.keep) is filtered out of files. Folders without any
    files still appear in child_folders because the marker keeps the prefix
    alive at the delimiter boundary."""
    resp = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=prefix, Delimiter="/")
    files: list[dict] = []
    for obj in resp.get("Contents", []) or []:
        key = obj["Key"]
        if key == prefix or key.endswith("/"):
            continue
        name = key[len(prefix):]
        if name == FOLDER_MARKER or "/" in name:
            continue
        files.append(
            {
                "name": name,
                "size": obj["Size"],
                "last_modified": obj["LastModified"],
            }
        )
    folders: list[str] = []
    for cp in resp.get("CommonPrefixes", []) or []:
        p = cp.get("Prefix", "")
        if not p.startswith(prefix):
            continue
        rest = p[len(prefix):].rstrip("/")
        if rest and "/" not in rest:
            folders.append(rest)
    folders.sort()
    files.sort(key=lambda f: f["last_modified"], reverse=True)
    return files, folders


@app.get("/api/files")
async def api_list_files(folder: str = "", _: str = Depends(require_user)):
    folder = folder.strip()
    prefix = _prefix_for(folder or None)
    try:
        files, child_folders = _list_one_level(prefix)
    except ClientError as exc:
        log.exception("list failed")
        raise HTTPException(500, f"list failed: {exc}")

    return {
        "current_folder": folder,
        "folders": [] if folder else child_folders,
        "files": [
            {
                "name": f["name"],
                "size": f["size"],
                "last_modified": f["last_modified"].isoformat(),
            }
            for f in files
        ],
    }


class UploadURLRequest(BaseModel):
    filename: str
    folder: Optional[str] = None


@app.post("/api/files/upload-url")
async def api_upload_url(req: UploadURLRequest, _: str = Depends(require_user)):
    name = _safe_filename(req.filename)
    prefix = _prefix_for(req.folder or None)
    key = f"{prefix}{name}"
    try:
        # NOTE: deliberately omit ContentType from Params so the signature
        # only covers Bucket+Key. The browser can then send any Content-Type
        # without breaking signature validation. Tigris stores whatever the
        # browser sends and returns the same on download.
        url = s3.generate_presigned_url(
            "put_object",
            Params={"Bucket": S3_BUCKET, "Key": key},
            ExpiresIn=PRESIGN_EXPIRES,
            HttpMethod="PUT",
        )
    except ClientError as exc:
        log.exception("presign upload failed")
        raise HTTPException(500, f"presign failed: {exc}")
    return {"url": url, "key": key, "expires_in": PRESIGN_EXPIRES}


@app.get("/api/files/download/{filename:path}")
async def api_download(filename: str, folder: str = "", _: str = Depends(require_user)):
    name = _safe_filename(filename)
    prefix = _prefix_for(folder or None)
    key = f"{prefix}{name}"
    try:
        url = s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": S3_BUCKET,
                "Key": key,
                "ResponseContentDisposition": f'attachment; filename="{name}"',
            },
            ExpiresIn=PRESIGN_EXPIRES,
            HttpMethod="GET",
        )
    except ClientError as exc:
        log.exception("presign download failed")
        raise HTTPException(500, f"presign failed: {exc}")
    return RedirectResponse(url, status_code=307)


_INLINE_CONTENT_TYPES = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
}


@app.get("/api/files/stream/{filename:path}")
async def api_stream(filename: str, folder: str = "", _: str = Depends(require_user)):
    """Inline-disposition presigned URL — used by the in-page <audio> element."""
    name = _safe_filename(filename)
    prefix = _prefix_for(folder or None)
    key = f"{prefix}{name}"
    ext = name.lower().rsplit(".", 1)
    content_type = _INLINE_CONTENT_TYPES.get(f".{ext[1]}" if len(ext) == 2 else "", "application/octet-stream")
    try:
        url = s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": S3_BUCKET,
                "Key": key,
                "ResponseContentDisposition": f'inline; filename="{name}"',
                "ResponseContentType": content_type,
            },
            ExpiresIn=PRESIGN_EXPIRES,
            HttpMethod="GET",
        )
    except ClientError as exc:
        log.exception("presign stream failed")
        raise HTTPException(500, f"presign failed: {exc}")
    return RedirectResponse(url, status_code=307)


class RenameRequest(BaseModel):
    old: str
    new: str
    folder: Optional[str] = None


@app.post("/api/files/rename")
async def api_rename(req: RenameRequest, user: str = Depends(require_user)):
    old_name = _safe_filename(req.old)
    new_name = _safe_filename(req.new)
    if old_name == new_name:
        return {"renamed": new_name}

    prefix = _prefix_for(req.folder or None)
    old_key = f"{prefix}{old_name}"
    new_key = f"{prefix}{new_name}"

    try:
        s3.head_object(Bucket=S3_BUCKET, Key=new_key)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if code not in ("404", "NoSuchKey", "NotFound") and status != 404:
            log.exception("head_object failed during rename")
            raise HTTPException(500, f"rename precheck failed: {exc}")
    else:
        raise HTTPException(409, f"a file named {new_name} already exists")

    try:
        s3.copy_object(
            Bucket=S3_BUCKET,
            Key=new_key,
            CopySource={"Bucket": S3_BUCKET, "Key": old_key},
        )
        s3.delete_object(Bucket=S3_BUCKET, Key=old_key)
    except ClientError as exc:
        log.exception("rename failed")
        raise HTTPException(500, f"rename failed: {exc}")

    log.info("renamed by %s***: %s -> %s", user[:3], old_name, new_name)
    return {"renamed": new_name}


class MoveRequest(BaseModel):
    filename: str
    from_folder: Optional[str] = None
    to_folder: Optional[str] = None


@app.post("/api/files/move")
async def api_move(req: MoveRequest, user: str = Depends(require_user)):
    name = _safe_filename(req.filename)
    src_prefix = _prefix_for(req.from_folder or None)
    dst_prefix = _prefix_for(req.to_folder or None)
    if src_prefix == dst_prefix:
        return {"moved": name, "to_folder": req.to_folder or ""}

    src_key = f"{src_prefix}{name}"
    dst_key = f"{dst_prefix}{name}"

    try:
        s3.head_object(Bucket=S3_BUCKET, Key=dst_key)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if code not in ("404", "NoSuchKey", "NotFound") and status != 404:
            log.exception("head_object failed during move")
            raise HTTPException(500, f"move precheck failed: {exc}")
    else:
        raise HTTPException(
            409, f"a file named {name} already exists in destination"
        )

    try:
        s3.copy_object(
            Bucket=S3_BUCKET,
            Key=dst_key,
            CopySource={"Bucket": S3_BUCKET, "Key": src_key},
        )
        s3.delete_object(Bucket=S3_BUCKET, Key=src_key)
    except ClientError as exc:
        log.exception("move failed")
        raise HTTPException(500, f"move failed: {exc}")

    log.info(
        "moved by %s***: %s [%s -> %s]",
        user[:3],
        name,
        req.from_folder or "/",
        req.to_folder or "/",
    )
    return {"moved": name, "to_folder": req.to_folder or ""}


@app.delete("/api/files/{filename:path}")
async def api_delete(filename: str, folder: str = "", user: str = Depends(require_user)):
    name = _safe_filename(filename)
    prefix = _prefix_for(folder or None)
    key = f"{prefix}{name}"
    try:
        # Bucket versioning is on, so this creates a delete marker — the
        # actual data is recoverable for 30 days via the lifecycle rule.
        s3.delete_object(Bucket=S3_BUCKET, Key=key)
    except ClientError as exc:
        log.exception("delete failed")
        raise HTTPException(500, f"delete failed: {exc}")
    log.info("deleted by %s***: %s", user[:3], key)
    return {"deleted": name, "recoverable_until_days": 30}


# ── Folder API ────────────────────────────────────────────────────────────────


class FolderCreateRequest(BaseModel):
    name: str


@app.post("/api/folders")
async def api_folder_create(req: FolderCreateRequest, user: str = Depends(require_user)):
    name = _safe_folder(req.name)
    prefix = f"{S3_PREFIX}{name}/"
    try:
        existing = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=prefix, MaxKeys=1)
    except ClientError as exc:
        log.exception("folder existence check failed")
        raise HTTPException(500, f"folder create failed: {exc}")
    if existing.get("KeyCount", 0) > 0:
        raise HTTPException(409, f"folder {name} already exists")

    try:
        s3.put_object(Bucket=S3_BUCKET, Key=f"{prefix}{FOLDER_MARKER}", Body=b"")
    except ClientError as exc:
        log.exception("folder create failed")
        raise HTTPException(500, f"folder create failed: {exc}")

    log.info("folder created by %s***: %s", user[:3], name)
    return {"created": name}


@app.delete("/api/folders/{name}")
async def api_folder_delete(name: str, user: str = Depends(require_user)):
    folder = _safe_folder(name)
    prefix = f"{S3_PREFIX}{folder}/"

    try:
        resp = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=prefix)
    except ClientError as exc:
        log.exception("folder list failed during delete")
        raise HTTPException(500, f"folder delete failed: {exc}")

    contents = resp.get("Contents", []) or []
    real_files = [
        o for o in contents
        if not o["Key"].endswith(f"/{FOLDER_MARKER}") and o["Key"] != prefix
    ]
    if real_files:
        raise HTTPException(
            409, f"folder is not empty ({len(real_files)} files)"
        )

    for obj in contents:
        try:
            s3.delete_object(Bucket=S3_BUCKET, Key=obj["Key"])
        except ClientError as exc:
            log.warning(
                "folder delete: failed to remove %s: %s", obj["Key"], exc
            )

    log.info("folder deleted by %s***: %s", user[:3], folder)
    return {"deleted": folder}


class FolderRenameRequest(BaseModel):
    old: str
    new: str


@app.post("/api/folders/rename")
async def api_folder_rename(
    req: FolderRenameRequest, user: str = Depends(require_user)
):
    old = _safe_folder(req.old)
    new = _safe_folder(req.new)
    if old == new:
        return {"renamed": new}

    old_prefix = f"{S3_PREFIX}{old}/"
    new_prefix = f"{S3_PREFIX}{new}/"

    try:
        clash = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=new_prefix, MaxKeys=1)
    except ClientError as exc:
        log.exception("folder rename precheck failed")
        raise HTTPException(500, f"folder rename failed: {exc}")
    if clash.get("KeyCount", 0) > 0:
        raise HTTPException(409, f"folder {new} already exists")

    try:
        resp = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=old_prefix)
    except ClientError as exc:
        log.exception("folder list failed during rename")
        raise HTTPException(500, f"folder rename failed: {exc}")

    contents = resp.get("Contents", []) or []
    if not contents:
        raise HTTPException(404, f"folder {old} not found")

    for obj in contents:
        old_key = obj["Key"]
        rest = old_key[len(old_prefix):]
        new_key = f"{new_prefix}{rest}"
        try:
            s3.copy_object(
                Bucket=S3_BUCKET,
                Key=new_key,
                CopySource={"Bucket": S3_BUCKET, "Key": old_key},
            )
            s3.delete_object(Bucket=S3_BUCKET, Key=old_key)
        except ClientError as exc:
            log.exception("folder rename failed mid-way at %s", old_key)
            raise HTTPException(
                500, f"folder rename failed mid-way at {rest}: {exc}"
            )

    log.info("folder renamed by %s***: %s -> %s", user[:3], old, new)
    return {"renamed": new}
