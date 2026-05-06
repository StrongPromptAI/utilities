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

def _load_clients() -> dict:
    """Load OIDC client registrations from env vars. Each name N requires
    OIDC_CLIENT_N_ID, OIDC_CLIENT_N_SECRET, OIDC_CLIENT_N_REDIRECT — any
    name with all three present is registered. Unknown names are skipped.

    Names:
      OPENWEBUI — oxp.chat
      SFTPGO    — legacy oxp.files (decommissioned 2026-05-06; env vars may
                   still be present and are tolerated for backward compat)
      FILES     — new FastAPI oxp.files app
    """
    clients = {}
    for name in ("OPENWEBUI", "SFTPGO", "FILES"):
        cid = os.environ.get(f"OIDC_CLIENT_{name}_ID")
        secret = os.environ.get(f"OIDC_CLIENT_{name}_SECRET")
        redirect = os.environ.get(f"OIDC_CLIENT_{name}_REDIRECT")
        if cid and secret and redirect:
            clients[cid] = {"secret": secret, "redirect_uri": redirect}
    return clients


CLIENTS = _load_clients()

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
    nonce       TEXT,
    expires_at  TIMESTAMPTZ NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS oidc_auth_codes (
    code         TEXT PRIMARY KEY,
    email        TEXT NOT NULL,
    client_id    TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    nonce        TEXT,
    expires_at   TIMESTAMPTZ NOT NULL,
    created_at   TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS oidc_sso_sessions (
    token        TEXT PRIMARY KEY,
    email        TEXT NOT NULL,
    expires_at   TIMESTAMPTZ NOT NULL,
    created_at   TIMESTAMPTZ DEFAULT now(),
    last_used_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_oidc_sso_sessions_email ON oidc_sso_sessions(email);
CREATE INDEX IF NOT EXISTS idx_oidc_sso_sessions_expires ON oidc_sso_sessions(expires_at);
ALTER TABLE oidc_sessions   ADD COLUMN IF NOT EXISTS nonce TEXT;
ALTER TABLE oidc_auth_codes ADD COLUMN IF NOT EXISTS nonce TEXT;
"""

# SSO cookie config
SSO_COOKIE_NAME = "oxp_sso"
SSO_COOKIE_TTL_SECONDS = 30 * 24 * 3600  # 30 days

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
# "Operating-room minimalism" — refined dark with brand red (#c71a2f) as a
# single sharp accent. Palette lifted from orthokinetix.net. Logo embedded as
# inline base64 + filtered to white via CSS so it works as a faded watermark
# without a separate static-asset endpoint.

_LOGO_DATA_URI = "data:image/png;base64,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"

_CLIENT_TITLES = {
    "sftpgo": "OXP File Drop",
    "files": "OXP File Drop",
    "openwebui": "OXP Chat",
}


def _title_for(client_id: str) -> str:
    return _CLIENT_TITLES.get(client_id, "OXP")


_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,300;9..144,500;9..144,700&family=Manrope:wght@300;400;500;700&family=JetBrains+Mono:wght@500;700&display=swap');

:root {
  --brand-red: #c71a2f;
  --brand-warm: #fee7b5;
  --base-bg: #0a0b0d;
  --panel-bg: rgba(20, 21, 24, 0.72);
  --panel-border: rgba(255, 255, 255, 0.06);
  --text-primary: #f5f1e8;
  --text-muted: #8a8a8a;
  --input-line: #2a2b2f;
}

* { box-sizing: border-box; }

html, body {
  margin: 0;
  min-height: 100vh;
  font-family: 'Manrope', sans-serif;
  font-weight: 400;
  color: var(--text-primary);
  background: var(--base-bg);
  -webkit-font-smoothing: antialiased;
}

body {
  background:
    radial-gradient(ellipse at 50% 35%, rgba(199, 26, 47, 0.18) 0%, transparent 55%),
    linear-gradient(180deg, #0a0b0d 0%, #131418 100%);
  position: relative;
  overflow-x: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px 20px;
}

body::before {
  content: '';
  position: fixed;
  inset: 0;
  background-image: url('LOGO_PLACEHOLDER');
  background-repeat: no-repeat;
  background-position: 110% 95%;
  background-size: 60vmin auto;
  filter: brightness(0) invert(1);
  opacity: 0.04;
  pointer-events: none;
  z-index: 0;
}

body::after {
  content: '';
  position: fixed;
  inset: 0;
  background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='180' height='180'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2' /><feColorMatrix values='0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 0 0 0 0.45 0' /></filter><rect width='100%' height='100%' filter='url(%23n)' /></svg>");
  opacity: 0.05;
  pointer-events: none;
  mix-blend-mode: overlay;
  z-index: 0;
}

main {
  position: relative;
  z-index: 1;
  width: 100%;
  max-width: 420px;
  padding: 56px 44px 48px;
  background: var(--panel-bg);
  border: 1px solid var(--panel-border);
  border-radius: 2px;
  backdrop-filter: blur(18px) saturate(140%);
  -webkit-backdrop-filter: blur(18px) saturate(140%);
  box-shadow:
    0 1px 0 rgba(255, 255, 255, 0.04) inset,
    0 30px 80px -20px rgba(0, 0, 0, 0.5);
}

.title {
  font-family: 'Fraunces', serif;
  font-weight: 500;
  font-size: clamp(28px, 5vw, 38px);
  letter-spacing: -0.01em;
  line-height: 1.05;
  margin: 0 0 14px;
  color: var(--text-primary);
}

.bar {
  width: 48px;
  height: 2px;
  background: var(--brand-red);
  border: 0;
  margin: 0 0 22px;
}

.subtitle {
  font-size: 14px;
  font-weight: 400;
  line-height: 1.55;
  color: var(--text-muted);
  margin: 0 0 30px;
  letter-spacing: 0.005em;
}

.err {
  font-size: 13px;
  color: var(--brand-red);
  background: rgba(199, 26, 47, 0.08);
  border-left: 2px solid var(--brand-red);
  padding: 10px 14px;
  margin: 0 0 22px;
  font-weight: 500;
}

label {
  display: block;
  font-size: 11px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--text-muted);
  margin-bottom: 8px;
  font-weight: 500;
}

input[type="email"], input[type="text"] {
  width: 100%;
  padding: 12px 0 14px;
  font-family: 'Manrope', sans-serif;
  font-size: 16px;
  font-weight: 400;
  color: var(--text-primary);
  background: transparent;
  border: 0;
  border-bottom: 1px solid var(--input-line);
  border-radius: 0;
  outline: none;
  transition: border-color 200ms ease, box-shadow 200ms ease;
  margin-bottom: 28px;
}

input[type="email"]::placeholder,
input[type="text"]::placeholder {
  color: #4a4b4f;
  font-weight: 300;
}

input[type="email"]:focus, input[type="text"]:focus {
  border-bottom-color: var(--brand-red);
  box-shadow: 0 1px 0 0 var(--brand-red);
}

input[name="code"] {
  font-family: 'JetBrains Mono', monospace;
  font-weight: 500;
  font-size: 30px;
  text-align: center;
  letter-spacing: 0.4em;
  padding-left: 0.4em;
  color: var(--brand-warm);
}

button {
  width: 100%;
  padding: 16px;
  background: var(--brand-red);
  color: #fff;
  border: 0;
  border-radius: 0;
  font-family: 'Manrope', sans-serif;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  cursor: pointer;
  transition: transform 120ms ease, box-shadow 200ms ease, background 200ms ease;
}

button:hover {
  background: #d62540;
  box-shadow: 0 6px 24px -8px rgba(199, 26, 47, 0.55);
  transform: translateY(-1px);
}

button:active { transform: translateY(0); }

.restart {
  margin-top: 24px;
  text-align: center;
  font-size: 12px;
  letter-spacing: 0.04em;
  color: var(--text-muted);
}

.restart a {
  color: var(--brand-warm);
  text-decoration: none;
  border-bottom: 1px solid rgba(254, 231, 181, 0.3);
  padding-bottom: 1px;
}

.restart a:hover { border-bottom-color: var(--brand-warm); }

.brand-foot {
  margin: 36px 0 0;
  text-align: center;
  font-size: 9px;
  letter-spacing: 0.32em;
  text-transform: uppercase;
  color: rgba(245, 241, 232, 0.18);
  font-weight: 500;
}

@media (max-width: 480px) {
  main { padding: 40px 28px 36px; border-radius: 0; max-width: 100%; }
}
""".replace("LOGO_PLACEHOLDER", _LOGO_DATA_URI)


def _page(title: str, body_html: str) -> str:
    return (
        f'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{title}</title><style>{_CSS}</style></head>'
        f'<body><main>{body_html}</main></body></html>'
    )


def _email_form(client_id, redirect_uri, state, response_type, scope, nonce="", error=""):
    title = _title_for(client_id)
    err = f'<div class="err">{error}</div>' if error else ""
    body = f"""
      <h1 class="title">{title}</h1>
      <hr class="bar">
      <p class="subtitle">Enter your email to receive a one-time sign-in code.</p>
      {err}
      <form method="post" action="/authorize">
        <input type="hidden" name="client_id" value="{client_id}">
        <input type="hidden" name="redirect_uri" value="{redirect_uri}">
        <input type="hidden" name="state" value="{state}">
        <input type="hidden" name="nonce" value="{nonce}">
        <input type="hidden" name="response_type" value="{response_type}">
        <input type="hidden" name="scope" value="{scope}">
        <label for="email">Email</label>
        <input id="email" type="email" name="email" placeholder="you@orthoxpress.com" required autofocus>
        <button type="submit">Send code</button>
      </form>
      <p class="brand-foot">Orthokinetix · OrthoXpress</p>
    """
    return _page(f"Sign in — {title}", body)


def _otp_form(session_id, error="", restart_url="", client_id=""):
    title = _title_for(client_id)
    err = f'<div class="err">{error}</div>' if error else ""
    restart = (
        f'<p class="restart"><a href="{restart_url}">Start over</a></p>'
        if restart_url else ""
    )
    body = f"""
      <h1 class="title">{title}</h1>
      <hr class="bar">
      <p class="subtitle">Check your inbox for the 6-digit sign-in code. It expires in 10 minutes.</p>
      {err}
      <form method="post" action="/otp">
        <input type="hidden" name="session_id" value="{session_id}">
        <label for="code">Sign-in code</label>
        <input id="code" type="text" name="code" maxlength="6" placeholder="000000"
               required autofocus inputmode="numeric" pattern="[0-9]{{6}}" autocomplete="one-time-code">
        <button type="submit">Verify</button>
      </form>
      {restart}
      <p class="brand-foot">Orthokinetix · OrthoXpress</p>
    """
    return _page(f"Verify — {title}", body)


# ── SSO + auth-code helpers ───────────────────────────────────────────────────

async def _issue_auth_code(conn, email: str, client_id: str, redirect_uri: str, nonce: str) -> str:
    """Insert a fresh auth code for (email, client_id) and return it."""
    auth_code = secrets.token_urlsafe(32)
    auth_expires = datetime.now(timezone.utc) + timedelta(minutes=5)
    await conn.execute(
        """INSERT INTO oidc_auth_codes (code, email, client_id, redirect_uri, nonce, expires_at)
           VALUES ($1,$2,$3,$4,$5,$6)""",
        auth_code, email, client_id, redirect_uri, nonce, auth_expires,
    )
    return auth_code


def _redirect_with_code(redirect_uri: str, auth_code: str, state: str) -> RedirectResponse:
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(f"{redirect_uri}{sep}code={auth_code}&state={state}", status_code=303)


def _set_sso_cookie(response, token: str) -> None:
    response.set_cookie(
        key=SSO_COOKIE_NAME,
        value=token,
        max_age=SSO_COOKIE_TTL_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


async def _sso_email_from_cookie(token: str | None) -> str | None:
    """Return the email associated with a valid SSO token, or None.

    Wrapped in a broad try/except so any DB hiccup falls through to the email
    form rather than erroring out the entire login surface.
    """
    if not token:
        return None
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT email FROM oidc_sso_sessions WHERE token=$1 AND expires_at > now()",
                token,
            )
            if row:
                # Refresh last_used_at; non-critical, don't await failures
                try:
                    await conn.execute(
                        "UPDATE oidc_sso_sessions SET last_used_at = now() WHERE token=$1",
                        token,
                    )
                except Exception:
                    pass
                return row["email"]
    except Exception as exc:
        logger.warning("SSO cookie check failed: %s", exc)
    return None


# ── Authorize ─────────────────────────────────────────────────────────────────

@app.get("/authorize")
async def authorize_get(
    request: Request,
    client_id: str,
    redirect_uri: str,
    response_type: str = "code",
    state: str = "",
    scope: str = "openid",
    nonce: str = "",
):
    if client_id not in CLIENTS:
        raise HTTPException(400, "unknown client_id")
    if CLIENTS[client_id]["redirect_uri"] != redirect_uri:
        raise HTTPException(400, "invalid redirect_uri")

    # SSO short-circuit: if the user has a valid SSO cookie at this IdP host,
    # skip the email form and issue an auth code immediately.
    sso_token = request.cookies.get(SSO_COOKIE_NAME)
    email = await _sso_email_from_cookie(sso_token)
    if email:
        async with pool.acquire() as conn:
            auth_code = await _issue_auth_code(conn, email, client_id, redirect_uri, nonce)
        logger.info("SSO auth code issued for %s*** client=%s", email[:3], client_id)
        return _redirect_with_code(redirect_uri, auth_code, state)

    return HTMLResponse(_email_form(client_id, redirect_uri, state, response_type, scope, nonce))


@app.post("/authorize", response_class=HTMLResponse)
async def authorize_post(
    client_id: str = Form(...),
    redirect_uri: str = Form(...),
    state: str = Form(""),
    nonce: str = Form(""),
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
               (session_id, email, otp, client_id, redirect_uri, state, nonce, expires_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
            session_id, email, otp, client_id, redirect_uri, state, nonce, expires_at,
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
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT client_id FROM oidc_sessions WHERE session_id=$1", session_id
        )
    client_id = row["client_id"] if row else ""
    return HTMLResponse(_otp_form(session_id, client_id=client_id))


@app.post("/otp")
async def otp_post(
    session_id: str = Form(...),
    code: str = Form(...),
):
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM oidc_sessions WHERE session_id=$1",
            session_id,
        )
        if not row:
            # Session gone entirely — send back to a generic restart
            return HTMLResponse(
                _otp_form(session_id, "Session not found. Please start over.", restart_url="/", client_id=""),
                status_code=400,
            )
        if row["expires_at"].replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
            restart = (
                f"/authorize?client_id={row['client_id']}"
                f"&redirect_uri={row['redirect_uri']}"
                f"&state={row['state'] or ''}&response_type=code"
            )
            return HTMLResponse(
                _otp_form(session_id, "Code expired — request a new one.", restart_url=restart, client_id=row["client_id"]),
                status_code=400,
            )
        if row["otp"] != code.strip():
            return HTMLResponse(
                _otp_form(session_id, "Incorrect code — try again.", client_id=row["client_id"]),
                status_code=400,
            )

        auth_code = await _issue_auth_code(
            conn, row["email"], row["client_id"], row["redirect_uri"], row["nonce"],
        )
        await conn.execute("DELETE FROM oidc_sessions WHERE session_id=$1", session_id)
        # Mint SSO token tied to this email; the cookie is set on the response below.
        sso_token = secrets.token_urlsafe(32)
        sso_expires = datetime.now(timezone.utc) + timedelta(seconds=SSO_COOKIE_TTL_SECONDS)
        await conn.execute(
            """INSERT INTO oidc_sso_sessions (token, email, expires_at)
               VALUES ($1,$2,$3)""",
            sso_token, row["email"], sso_expires,
        )
        redirect_uri = row["redirect_uri"]
        state = row["state"] or ""
        logger.info("Auth code issued for %s*** client=%s", row["email"][:3], row["client_id"])

    response = _redirect_with_code(redirect_uri, auth_code, state)
    _set_sso_cookie(response, sso_token)
    return response


# ── Logout ────────────────────────────────────────────────────────────────────

@app.get("/logout")
async def logout(request: Request, redirect_uri: str = ""):
    """Clear the SSO cookie and delete the server-side session row.

    `redirect_uri` is optional; if provided it must match a registered client's
    redirect_uri (basic whitelist to prevent open-redirect abuse).
    """
    sso_token = request.cookies.get(SSO_COOKIE_NAME)
    if sso_token:
        try:
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM oidc_sso_sessions WHERE token=$1", sso_token)
        except Exception as exc:
            logger.warning("logout DB delete failed: %s", exc)

    # Pick a destination — registered client redirect_uri, or a no-op text response
    target = ""
    if redirect_uri:
        for cfg in CLIENTS.values():
            if cfg["redirect_uri"] == redirect_uri:
                target = redirect_uri
                break

    if target:
        response = RedirectResponse(target, status_code=303)
    else:
        response = HTMLResponse("<html><body><p>Signed out.</p></body></html>")
    response.delete_cookie(SSO_COOKIE_NAME, path="/")
    return response


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
        "exp": now + 2592000,
        "email": email,
        "email_verified": True,
        "name": email.split("@")[0],
    }
    # Echo nonce back into ID token if the client sent one at /authorize.
    # Required by OIDC spec — authlib (OpenWebUI) rejects tokens missing the nonce
    # it originally sent. Also required by strict Nextcloud oidc_login configurations.
    if row["nonce"]:
        claims["nonce"] = row["nonce"]
    id_token = jwt.encode(claims, _raw_pem, algorithm="RS256", headers={"kid": KID})
    access_token = jwt.encode(
        {**claims, "exp": now + 2592000}, _raw_pem, algorithm="RS256", headers={"kid": KID}
    )
    return JSONResponse({
        "access_token": access_token,
        "id_token": id_token,
        "token_type": "Bearer",
        "expires_in": 2592000,
    })


# ── SFTPGo pre-login hook ─────────────────────────────────────────────────────
# SFTPGo's data provider calls this URL before a login attempt. We return a
# user spec for any HTTP-protocol login with an email-shaped username so SFTPGo
# auto-provisions the user on first OIDC redirect. The user's permissions and
# filesystem (S3 with per-user prefix) come from the `oidc-users` group, so we
# only set group membership here — leaving permissions/filesystem unset means
# group settings flow through unchanged.
#
# Protocols other than HTTP (SFTP/FTP/DAV) get an empty response so the hook
# can't be used to backdoor SFTP-only auth — those ports are disabled in our
# deployment anyway, this is belt-and-braces.

# Email domain whitelist for SFTPGo auto-provisioning. Only addresses on
# these domains get a user record created via the prelogin hook. Edit + redeploy
# to add or remove a domain.
_ALLOWED_SFTPGO_DOMAINS = (
    "orthoxpress.com",
    "strongprompt.ai",
    "bilberryindustries.com",
)


@app.post("/sftpgo/prelogin")
async def sftpgo_prelogin(request: Request):
    body = await request.json()
    logger.info("sftpgo prelogin called: %s", body)
    username = body.get("username", "")

    if "@" not in username:
        logger.info("sftpgo prelogin: not an email, returning empty (use-existing)")
        return JSONResponse({}, status_code=200)

    domain = username.rsplit("@", 1)[-1].lower()
    if domain not in _ALLOWED_SFTPGO_DOMAINS:
        logger.warning(
            "sftpgo prelogin: domain %s not in whitelist, refusing auto-provision for %s",
            domain, username,
        )
        return JSONResponse({}, status_code=200)

    # SFTPGo's pre-login validator runs the full User schema check before
    # creating the record. We deliberately omit `home_dir` and `filesystem`
    # so the oidc-users primary group's S3 config (key_prefix=shared/) takes
    # over — without that omission the user lands on ephemeral local disk.
    spec = {
        "username": username,
        "email": username,
        "status": 1,
        "groups": [{"name": "oidc-users", "type": 1}],
        "permissions": {"/": ["list", "download", "upload", "overwrite", "delete"]},
    }
    logger.info("sftpgo prelogin: returning spec for %s", username)
    return JSONResponse(spec)


# ── Userinfo ──────────────────────────────────────────────────────────────────

@app.get("/userinfo")
async def userinfo(request: Request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "Missing bearer token")
    token_str = auth[7:]
    try:
        # We signed this token ourselves; we trust all its claims. Skip audience
        # check — without this, PyJWT rejects any token that carries an `aud`
        # claim when no audience= is passed to decode. (Nextcloud's oidc_login
        # then gets 401 and shows "communication to retrieve user data failed".)
        claims = jwt.decode(
            token_str, _PUBLIC_KEY_PEM, algorithms=["RS256"],
            options={"verify_aud": False},
        )
    except Exception as e:
        logger.warning("userinfo token decode failed: %s", e)
        raise HTTPException(401, "Invalid or expired token")
    return {
        "sub": claims["sub"],
        "email": claims["email"],
        "email_verified": True,
        "name": claims.get("name", ""),
    }
