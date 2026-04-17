import asyncio
import base64
import hashlib
import logging
import os
import secrets
import string
from datetime import datetime, timedelta, timezone

import asyncpg
import resend
from cryptography.hazmat.primitives import serialization
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from jose import jwt

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Config ───────────────────────────────────────────────────────────────────

DATABASE_URL = os.environ["DATABASE_URL"]
RESEND_API_KEY = os.environ["RESEND_API_KEY"]
OIDC_ISSUER = os.environ["OIDC_ISSUER"].rstrip("/")
RESEND_FROM = os.environ.get("RESEND_FROM", "OXP Auth <noreply@auth.strongprompt.ai>")

# PEM stored with literal \n in Railway — unescape at startup
_raw_pem = os.environ["OIDC_PRIVATE_KEY"].replace("\\n", "\n")
if not _raw_pem.strip().startswith("-----"):
    # stored as base64 fallback
    _raw_pem = base64.b64decode(_raw_pem).decode()

CLIENTS = {
    os.environ["OIDC_CLIENT_OPENWEBUI_ID"]: {
        "secret": os.environ["OIDC_CLIENT_OPENWEBUI_SECRET"],
        "redirect_uri": os.environ["OIDC_CLIENT_OPENWEBUI_REDIRECT"],
    },
    os.environ["OIDC_CLIENT_NEXTCLOUD_ID"]: {
        "secret": os.environ["OIDC_CLIENT_NEXTCLOUD_SECRET"],
        "redirect_uri": os.environ["OIDC_CLIENT_NEXTCLOUD_REDIRECT"],
    },
}

# ── Key setup ────────────────────────────────────────────────────────────────

_private_key = serialization.load_pem_private_key(_raw_pem.encode(), password=None)
_public_key = _private_key.public_key()
_pub_nums = _public_key.public_numbers()
_pub_der = _public_key.public_bytes(
    serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
)
KID = hashlib.sha256(_pub_der).hexdigest()[:8]

_PUBLIC_KEY_PEM = _public_key.public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
).decode()


def _b64url(n: int) -> str:
    length = (n.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(n.to_bytes(length, "big")).rstrip(b"=").decode()


JWKS = {
    "keys": [
        {
            "kty": "RSA",
            "use": "sig",
            "kid": KID,
            "alg": "RS256",
            "n": _b64url(_pub_nums.n),
            "e": _b64url(_pub_nums.e),
        }
    ]
}

# ── DB ────────────────────────────────────────────────────────────────────────

pool: asyncpg.Pool = None

CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS oidc_sessions (
    session_id  TEXT PRIMARY KEY,
    email       TEXT NOT NULL,
    otp         TEXT NOT NULL,
    client_id   TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    state       TEXT,
    expires_at  TIMESTAMPTZ NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS oidc_auth_codes (
    code         TEXT PRIMARY KEY,
    email        TEXT NOT NULL,
    client_id    TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    expires_at   TIMESTAMPTZ NOT NULL,
    created_at   TIMESTAMPTZ DEFAULT now()
);
"""

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI()


@app.on_event("startup")
async def startup():
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL)
    async with pool.acquire() as conn:
        await conn.execute(CREATE_TABLES)
    resend.api_key = RESEND_API_KEY
    logger.info("oidc-otp ready — issuer=%s kid=%s", OIDC_ISSUER, KID)


@app.on_event("shutdown")
async def shutdown():
    await pool.close()


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"service": "oidc-otp"}


# ── OIDC discovery ────────────────────────────────────────────────────────────

@app.get("/.well-known/openid-configuration")
async def openid_configuration():
    return {
        "issuer": OIDC_ISSUER,
        "authorization_endpoint": f"{OIDC_ISSUER}/authorize",
        "token_endpoint": f"{OIDC_ISSUER}/token",
        "userinfo_endpoint": f"{OIDC_ISSUER}/userinfo",
        "jwks_uri": f"{OIDC_ISSUER}/.well-known/jwks.json",
        "response_types_supported": ["code"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "scopes_supported": ["openid", "email", "profile"],
        "token_endpoint_auth_methods_supported": ["client_secret_post"],
        "claims_supported": ["sub", "email", "name", "iss", "iat", "exp", "aud"],
    }


@app.get("/.well-known/jwks.json")
async def jwks():
    return JWKS


# ── HTML helpers ──────────────────────────────────────────────────────────────

_CSS = """
body{font-family:system-ui,sans-serif;max-width:400px;margin:80px auto;padding:0 20px}
h2{margin-bottom:4px}p{color:#555;margin-top:4px}
input{width:100%;padding:10px;margin:10px 0;box-sizing:border-box;font-size:16px;
      border:1px solid #ccc;border-radius:4px}
button{width:100%;padding:12px;background:#2563eb;color:#fff;border:none;
       border-radius:4px;font-size:16px;cursor:pointer}
button:hover{background:#1d4ed8}.err{color:#dc2626;margin-top:0}
"""


def _email_form(client_id, redirect_uri, state, response_type, scope, error=""):
    err = f'<p class="err">{error}</p>' if error else ""
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Sign In — OXP</title><style>{_CSS}</style></head><body>
<h2>Sign in to OXP</h2>
<p>Enter your email to receive a one-time code.</p>{err}
<form method="post" action="/authorize">
  <input type="hidden" name="client_id" value="{client_id}">
  <input type="hidden" name="redirect_uri" value="{redirect_uri}">
  <input type="hidden" name="state" value="{state}">
  <input type="hidden" name="response_type" value="{response_type}">
  <input type="hidden" name="scope" value="{scope}">
  <input type="email" name="email" placeholder="you@example.com" required autofocus>
  <button type="submit">Send Code</button>
</form></body></html>"""


def _otp_form(session_id, error=""):
    err = f'<p class="err">{error}</p>' if error else ""
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Enter Code — OXP</title><style>{_CSS}
input[name=code]{{font-size:28px;text-align:center;letter-spacing:10px}}
</style></head><body>
<h2>Enter your code</h2>
<p>Check your email for the 6-digit sign-in code.</p>{err}
<form method="post" action="/otp">
  <input type="hidden" name="session_id" value="{session_id}">
  <input type="text" name="code" maxlength="6" placeholder="000000"
         required autofocus inputmode="numeric" pattern="[0-9]{{6}}">
  <button type="submit">Verify</button>
</form></body></html>"""


# ── Authorize ─────────────────────────────────────────────────────────────────

@app.get("/authorize", response_class=HTMLResponse)
async def authorize_get(
    client_id: str,
    redirect_uri: str,
    response_type: str = "code",
    state: str = "",
    scope: str = "openid",
):
    if client_id not in CLIENTS:
        raise HTTPException(400, "unknown client_id")
    if CLIENTS[client_id]["redirect_uri"] != redirect_uri:
        raise HTTPException(400, "invalid redirect_uri")
    return HTMLResponse(_email_form(client_id, redirect_uri, state, response_type, scope))


@app.post("/authorize", response_class=HTMLResponse)
async def authorize_post(
    client_id: str = Form(...),
    redirect_uri: str = Form(...),
    state: str = Form(""),
    response_type: str = Form("code"),
    scope: str = Form("openid"),
    email: str = Form(...),
):
    if client_id not in CLIENTS:
        raise HTTPException(400, "unknown client_id")
    if CLIENTS[client_id]["redirect_uri"] != redirect_uri:
        raise HTTPException(400, "invalid redirect_uri")

    otp = "".join(secrets.choice(string.digits) for _ in range(6))
    session_id = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)

    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM oidc_sessions WHERE email=$1 AND expires_at < now()", email
        )
        await conn.execute(
            """INSERT INTO oidc_sessions
               (session_id, email, otp, client_id, redirect_uri, state, expires_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7)""",
            session_id, email, otp, client_id, redirect_uri, state, expires_at,
        )

    try:
        await asyncio.to_thread(resend.Emails.send, {
            "from": RESEND_FROM,
            "to": email,
            "subject": "Your OXP sign-in code",
            "html": (
                f"<p>Your sign-in code is:</p>"
                f"<p style='font-size:32px;letter-spacing:8px;font-weight:bold'>{otp}</p>"
                f"<p>This code expires in 10 minutes. Do not share it.</p>"
            ),
        })
        logger.info("OTP sent to %s***", email[:3])
    except Exception as exc:
        logger.error("Resend failed for %s***: %s", email[:3], exc)
        return HTMLResponse(
            _email_form(client_id, redirect_uri, state, response_type, scope,
                        "Failed to send code — please try again."),
            status_code=500,
        )

    return RedirectResponse(f"/otp?session_id={session_id}", status_code=303)


# ── OTP entry ─────────────────────────────────────────────────────────────────

@app.get("/otp", response_class=HTMLResponse)
async def otp_get(session_id: str):
    return HTMLResponse(_otp_form(session_id))


@app.post("/otp")
async def otp_post(
    session_id: str = Form(...),
    code: str = Form(...),
):
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM oidc_sessions WHERE session_id=$1 AND expires_at > now()",
            session_id,
        )
        if not row or row["otp"] != code.strip():
            return HTMLResponse(
                _otp_form(session_id, "Invalid or expired code — try again."),
                status_code=400,
            )

        auth_code = secrets.token_urlsafe(32)
        auth_expires = datetime.now(timezone.utc) + timedelta(minutes=5)
        await conn.execute(
            """INSERT INTO oidc_auth_codes (code, email, client_id, redirect_uri, expires_at)
               VALUES ($1,$2,$3,$4,$5)""",
            auth_code, row["email"], row["client_id"], row["redirect_uri"], auth_expires,
        )
        await conn.execute("DELETE FROM oidc_sessions WHERE session_id=$1", session_id)
        redirect_uri = row["redirect_uri"]
        state = row["state"] or ""
        logger.info("Auth code issued for %s*** client=%s", row["email"][:3], row["client_id"])

    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(
        f"{redirect_uri}{sep}code={auth_code}&state={state}", status_code=303
    )


# ── Token ─────────────────────────────────────────────────────────────────────

@app.post("/token")
async def token_endpoint(
    request: Request,
    grant_type: str = Form(...),
    code: str = Form(...),
    redirect_uri: str = Form(...),
    client_id: str = Form(None),
    client_secret: str = Form(None),
):
    # Support Basic auth as fallback (some clients prefer it)
    if not client_id or not client_secret:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Basic "):
            decoded = base64.b64decode(auth[6:]).decode()
            client_id, _, client_secret = decoded.partition(":")

    if grant_type != "authorization_code":
        raise HTTPException(400, detail={"error": "unsupported_grant_type"})
    if client_id not in CLIENTS:
        raise HTTPException(401, detail={"error": "invalid_client"})
    if CLIENTS[client_id]["secret"] != client_secret:
        raise HTTPException(401, detail={"error": "invalid_client"})

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM oidc_auth_codes WHERE code=$1 AND expires_at > now()", code
        )
        if not row:
            raise HTTPException(400, detail={"error": "invalid_grant"})
        if row["client_id"] != client_id or row["redirect_uri"] != redirect_uri:
            raise HTTPException(400, detail={"error": "invalid_grant"})
        await conn.execute("DELETE FROM oidc_auth_codes WHERE code=$1", code)

    now = int(datetime.now(timezone.utc).timestamp())
    email = row["email"]
    claims = {
        "iss": OIDC_ISSUER,
        "sub": email,
        "aud": client_id,
        "iat": now,
        "exp": now + 3600,
        "email": email,
        "email_verified": True,
        "name": email.split("@")[0],
    }
    id_token = jwt.encode(claims, _raw_pem, algorithm="RS256", headers={"kid": KID})
    access_token = jwt.encode(
        {**claims, "exp": now + 3600}, _raw_pem, algorithm="RS256", headers={"kid": KID}
    )
    return JSONResponse({
        "access_token": access_token,
        "id_token": id_token,
        "token_type": "Bearer",
        "expires_in": 3600,
    })


# ── Userinfo ──────────────────────────────────────────────────────────────────

@app.get("/userinfo")
async def userinfo(request: Request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "Missing bearer token")
    token_str = auth[7:]
    try:
        claims = jwt.decode(token_str, _PUBLIC_KEY_PEM, algorithms=["RS256"])
    except Exception:
        raise HTTPException(401, "Invalid or expired token")
    return {
        "sub": claims["sub"],
        "email": claims["email"],
        "email_verified": True,
        "name": claims.get("name", ""),
    }
