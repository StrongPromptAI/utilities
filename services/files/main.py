"""oxp.files — FastAPI app fronting a Tigris S3-compatible bucket.

Browser flow:
  /                            file browser (auth-gated, redirects to /oidc/login if no session)
  /?folder=<name>              same browser, scoped to one folder
  /oidc/login → /oidc/callback OAuth dance against oidc-otp
  /api/files[?folder=<name>]   list (JSON: {files, folders, current_folder})
  /api/files/upload-url        POST → presigned PUT URL (browser uploads direct to Tigris)
  /api/files/download/{name}   307 redirect to presigned GET URL
  /api/files/presign/{name}    JSON {url, expires_in} for the same presigned URL (sharable)
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

import asyncio
import json
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

THEME_COOKIE_NAME = "oxp_files_theme"
THEME_COOKIE_TTL = 365 * 24 * 3600
THEME_VALID = {"dark", "light"}
THEME_DEFAULT = "dark"

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

# Folders whose contents are publicly readable. NOTE: Tigris does not
# implement PutBucketPolicy (NotImplemented) and silently no-ops
# PutObjectAcl on a private bucket — the only working mechanism is a
# whole-bucket public-read setting at bucket creation. So a "public folder"
# only works if oxp.files is wired to a separate public-read bucket for
# these prefixes. Default is empty until that two-bucket setup exists;
# set PUBLIC_FOLDERS via env when the public bucket is configured.
PUBLIC_FOLDERS = {
    f.strip()
    for f in os.environ.get("PUBLIC_FOLDERS", "").split(",")
    if f.strip()
}

ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get("ALLOWED_ORIGINS", PUBLIC_BASE_URL).split(",")
    if o.strip()
]

PRESIGN_EXPIRES = 3600  # 1 hour

ACTIVITY_PREFIX = "_activity/"
ACTIVITY_ACTIONS = {
    "login", "logout", "upload", "download", "delete", "rename", "move",
    "presign",
}


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

    # Public-read bucket policy — designated folders are world-readable so
    # podcast mp3s (and similar) can be embedded in public sites with a
    # permanent URL instead of an expiring presigned one. Full replace, so
    # the app owns this config; do not hand-edit via Tigris console.
    if PUBLIC_FOLDERS:
        try:
            resources = [
                f"arn:aws:s3:::{S3_BUCKET}/{S3_PREFIX}{folder}/*"
                for folder in sorted(PUBLIC_FOLDERS)
            ]
            policy = json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Sid": "PublicReadDesignatedFolders",
                    "Effect": "Allow",
                    "Principal": "*",
                    "Action": ["s3:GetObject"],
                    "Resource": resources,
                }],
            })
            s3.put_bucket_policy(Bucket=S3_BUCKET, Policy=policy)
            log.info("public-read policy applied to: %s", sorted(PUBLIC_FOLDERS))
        except ClientError as exc:
            log.warning("put_bucket_policy failed: %s", exc)
    else:
        # If no public folders configured, strip any existing policy so we
        # don't silently leave a stale grant in place.
        try:
            s3.delete_bucket_policy(Bucket=S3_BUCKET)
        except ClientError:
            pass

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
    """
    Resolve the authenticated email from either the browser cookie or a
    Bearer JWT. Both paths verify against the same HS256 JWT_SECRET.

    Smoke (CLI path), with a script-minted JWT in $TOK:
        curl -fsSL -H "Authorization: Bearer $TOK" \\
             https://oxp.files.strongprompt.ai/api/files?folder=
    Expected: 200 + JSON file list. Omitting the header returns 401.
    """
    # Cookie path — browser sessions set oxp_sso via OIDC login.
    email = _verify_session_jwt(request.cookies.get(SESSION_COOKIE_NAME))
    if email:
        return email
    # Bearer path — CLI/script callers (e.g., thj ingest_from_oxp.sh) mint
    # the same HS256 JWT from JWT_SECRET and present it via Authorization
    # header. Same trust boundary, different transport.
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        email = _verify_session_jwt(auth_header[len("Bearer ") :])
        if email:
            return email
    raise HTTPException(status_code=401, detail="not authenticated")


# ── Theme ─────────────────────────────────────────────────────────────────────

def _read_theme(request: Request) -> str:
    val = request.cookies.get(THEME_COOKIE_NAME, "")
    return val if val in THEME_VALID else THEME_DEFAULT


# ── Activity log ──────────────────────────────────────────────────────────────
#
# One JSON object per event under _activity/YYYY-MM-DD/<ts>-<rand>.json. S3 has
# no atomic append, so a single-file JSONL would clobber under concurrent
# writes; per-event objects are race-free and the key prefix sorts
# chronologically. The _activity/ prefix lives outside S3_PREFIX (shared/) so
# events never appear in user-facing listings.

def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",", 1)[0].strip()
    if request.client:
        return request.client.host
    return ""


def _log_activity_blocking(
    *, user: str, action: str, file: Optional[str], folder: Optional[str], ip: str
) -> None:
    now = datetime.now(timezone.utc)
    event = {
        "ts": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "user": user,
        "action": action,
        "file": file,
        "folder": folder or "",
        "ip": ip,
    }
    key = (
        f"{ACTIVITY_PREFIX}{now.strftime('%Y-%m-%d')}/"
        f"{now.strftime('%H%M%S')}-{secrets.token_hex(4)}.json"
    )
    try:
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=key,
            Body=json.dumps(event).encode("utf-8"),
            ContentType="application/json",
        )
    except ClientError as exc:
        log.warning("activity log write failed (%s by %s): %s", action, user[:3], exc)


def log_activity(
    *,
    user: str,
    action: str,
    request: Optional[Request] = None,
    file: Optional[str] = None,
    folder: Optional[str] = None,
) -> None:
    """Fire-and-forget activity write. Never raises; never blocks the caller."""
    if action not in ACTIVITY_ACTIONS:
        log.warning("log_activity: unknown action %r", action)
        return
    ip = _client_ip(request) if request else ""
    asyncio.create_task(
        asyncio.to_thread(
            _log_activity_blocking,
            user=user, action=action, file=file, folder=folder, ip=ip,
        )
    )


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
            theme=_read_theme(request),
            public_folders=sorted(PUBLIC_FOLDERS),
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
    log_activity(user=email, action="login", request=request)
    return response


class ThemeRequest(BaseModel):
    theme: str


@app.post("/api/theme")
async def api_set_theme(req: ThemeRequest, user: str = Depends(require_user)):
    if req.theme not in THEME_VALID:
        raise HTTPException(400, "invalid theme")
    response = JSONResponse({"theme": req.theme})
    response.set_cookie(
        THEME_COOKIE_NAME,
        req.theme,
        max_age=THEME_COOKIE_TTL,
        httponly=False,  # client JS reads this for instant pre-paint swap
        secure=True,
        samesite="lax",
        path="/",
    )
    return response


@app.get("/activity", response_class=HTMLResponse)
async def activity_page(request: Request, day: str = ""):
    email = _verify_session_jwt(request.cookies.get(SESSION_COOKIE_NAME))
    if not email:
        return RedirectResponse("/oidc/login", status_code=303)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    chosen = (day or today).strip()
    try:
        datetime.strptime(chosen, "%Y-%m-%d")
    except ValueError:
        chosen = today

    events: list[dict] = []
    try:
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(
            Bucket=S3_BUCKET, Prefix=f"{ACTIVITY_PREFIX}{chosen}/"
        ):
            for obj in page.get("Contents", []) or []:
                try:
                    body = s3.get_object(Bucket=S3_BUCKET, Key=obj["Key"])["Body"].read()
                    events.append(json.loads(body))
                except (ClientError, json.JSONDecodeError) as exc:
                    log.warning("activity read skipped %s: %s", obj["Key"], exc)
    except ClientError as exc:
        log.exception("activity list failed")
        return HTMLResponse(
            templates.error_html("OXP Activity Log", f"Failed to list activity: {exc}"),
            status_code=500,
        )

    # Discover the most recent ~30 days that have any activity, for the picker.
    available_days: list[str] = []
    try:
        resp = s3.list_objects_v2(
            Bucket=S3_BUCKET, Prefix=ACTIVITY_PREFIX, Delimiter="/"
        )
        for cp in resp.get("CommonPrefixes", []) or []:
            p = cp.get("Prefix", "")
            d = p[len(ACTIVITY_PREFIX):].rstrip("/")
            if len(d) == 10 and d.count("-") == 2:
                available_days.append(d)
        available_days.sort(reverse=True)
        available_days = available_days[:30]
    except ClientError:
        available_days = [chosen]

    events.sort(key=lambda e: e.get("ts", ""), reverse=True)

    theme = _read_theme(request)
    return HTMLResponse(
        templates.activity_html(
            email=email,
            events=events,
            current_day=chosen,
            available_days=available_days,
            theme=theme,
        )
    )


@app.get("/logout")
async def logout(request: Request):
    """Clear local session + bounce through IdP /logout to clear the SSO cookie too."""
    email = _verify_session_jwt(request.cookies.get(SESSION_COOKIE_NAME))
    if email:
        log_activity(user=email, action="logout", request=request)
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
        "public_folders": sorted(PUBLIC_FOLDERS),
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


class UploadedRequest(BaseModel):
    filename: str
    folder: Optional[str] = None


@app.post("/api/files/uploaded")
async def api_uploaded(
    req: UploadedRequest, request: Request, user: str = Depends(require_user)
):
    """Client confirms a presigned upload landed in Tigris. Activity-log-only."""
    name = _safe_filename(req.filename)
    log_activity(
        user=user, action="upload", request=request,
        file=name, folder=req.folder or "",
    )
    return {"logged": name}


@app.get("/api/files/download/{filename:path}")
async def api_download(
    filename: str, request: Request, folder: str = "", user: str = Depends(require_user)
):
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
    log_activity(
        user=user, action="download", request=request,
        file=name, folder=folder or "",
    )
    return RedirectResponse(url, status_code=307)


@app.get("/api/files/presign/{filename:path}")
async def api_presign(
    filename: str, request: Request, folder: str = "", user: str = Depends(require_user)
):
    name = _safe_filename(filename)
    folder = folder.strip()
    prefix = _prefix_for(folder or None)
    key = f"{prefix}{name}"

    # Public folders short-circuit to a bare permanent URL — the bucket policy
    # (see lifespan) grants anonymous s3:GetObject on this prefix.
    if folder and folder in PUBLIC_FOLDERS:
        url = f"{S3_ENDPOINT.rstrip('/')}/{S3_BUCKET}/{key}"
        log_activity(
            user=user, action="presign", request=request,
            file=name, folder=folder,
        )
        return {"url": url, "kind": "public", "expires_in": None}

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
        log.exception("presign failed")
        raise HTTPException(500, f"presign failed: {exc}")
    log_activity(
        user=user, action="presign", request=request,
        file=name, folder=folder or "",
    )
    return {"url": url, "kind": "presigned", "expires_in": PRESIGN_EXPIRES}


_INLINE_CONTENT_TYPES = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".pdf": "application/pdf",
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
async def api_rename(
    req: RenameRequest, request: Request, user: str = Depends(require_user)
):
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
    log_activity(
        user=user, action="rename", request=request,
        file=f"{old_name} -> {new_name}", folder=req.folder or "",
    )
    return {"renamed": new_name}


class MoveRequest(BaseModel):
    filename: str
    from_folder: Optional[str] = None
    to_folder: Optional[str] = None


@app.post("/api/files/move")
async def api_move(
    req: MoveRequest, request: Request, user: str = Depends(require_user)
):
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
    log_activity(
        user=user, action="move", request=request,
        file=name,
        folder=f"{req.from_folder or '/'} -> {req.to_folder or '/'}",
    )
    return {"moved": name, "to_folder": req.to_folder or ""}


@app.delete("/api/files/{filename:path}")
async def api_delete(
    filename: str, request: Request, folder: str = "", user: str = Depends(require_user)
):
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
    log_activity(
        user=user, action="delete", request=request,
        file=name, folder=folder or "",
    )
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
