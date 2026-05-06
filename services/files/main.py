"""oxp.files — FastAPI app fronting a Tigris S3-compatible bucket.

Browser flow:
  /                            file browser (auth-gated, redirects to /oidc/login if no session)
  /oidc/login → /oidc/callback OAuth dance against oidc-otp
  /api/files                   list (JSON)
  /api/files/upload-url        POST → presigned PUT URL (browser uploads direct to Tigris)
  /api/files/download/{name}   307 redirect to presigned GET URL
  /api/files/{name}            DELETE
  /logout                      clear local session + redirect to oidc-otp /logout

Storage: single shared folder at `shared/` prefix. Every authenticated user
sees and can modify every other user's files (intentional, small allowlist).
Bucket versioning is enabled at startup so deletes are recoverable for 30 days.
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
async def home(request: Request):
    email = _verify_session_jwt(request.cookies.get(SESSION_COOKIE_NAME))
    if not email:
        return RedirectResponse("/oidc/login", status_code=303)

    try:
        resp = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=S3_PREFIX)
    except ClientError as exc:
        log.exception("list_objects_v2 failed")
        return HTMLResponse(
            templates.error_html("OXP File Drop", f"Failed to list files: {exc}"),
            status_code=500,
        )

    files = []
    for obj in resp.get("Contents", []):
        key = obj["Key"]
        if key == S3_PREFIX or key.endswith("/"):
            continue
        files.append(
            {
                "name": key[len(S3_PREFIX):],
                "size": obj["Size"],
                "last_modified": obj["LastModified"],
            }
        )
    files.sort(key=lambda f: f["last_modified"], reverse=True)

    return HTMLResponse(templates.file_browser_html(email, files))


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

def _safe_filename(name: str) -> str:
    """Reject path traversal and slashes — files live flat under shared/."""
    if not name or "/" in name or "\\" in name or ".." in name or name.startswith("."):
        raise HTTPException(400, "invalid filename")
    if len(name) > 255:
        raise HTTPException(400, "filename too long")
    return name


@app.get("/api/files")
async def api_list_files(_: str = Depends(require_user)):
    try:
        resp = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=S3_PREFIX)
    except ClientError as exc:
        log.exception("list failed")
        raise HTTPException(500, f"list failed: {exc}")

    files = []
    for obj in resp.get("Contents", []):
        key = obj["Key"]
        if key == S3_PREFIX or key.endswith("/"):
            continue
        files.append(
            {
                "name": key[len(S3_PREFIX):],
                "size": obj["Size"],
                "last_modified": obj["LastModified"].isoformat(),
            }
        )
    files.sort(key=lambda f: f["last_modified"], reverse=True)
    return files


class UploadURLRequest(BaseModel):
    filename: str


@app.post("/api/files/upload-url")
async def api_upload_url(req: UploadURLRequest, _: str = Depends(require_user)):
    name = _safe_filename(req.filename)
    key = f"{S3_PREFIX}{name}"
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
async def api_download(filename: str, _: str = Depends(require_user)):
    name = _safe_filename(filename)
    key = f"{S3_PREFIX}{name}"
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


@app.delete("/api/files/{filename:path}")
async def api_delete(filename: str, user: str = Depends(require_user)):
    name = _safe_filename(filename)
    key = f"{S3_PREFIX}{name}"
    try:
        # Bucket versioning is on, so this creates a delete marker — the
        # actual data is recoverable for 30 days via the lifecycle rule.
        s3.delete_object(Bucket=S3_BUCKET, Key=key)
    except ClientError as exc:
        log.exception("delete failed")
        raise HTTPException(500, f"delete failed: {exc}")
    log.info("deleted by %s***: %s", user[:3], name)
    return {"deleted": name, "recoverable_until_days": 30}
