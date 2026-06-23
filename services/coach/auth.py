"""Fail-closed OTP gate for the coach chat surface.

Verifies an HS256 session JWT (COACH_JWT_SECRET) carrying an `email`, then checks that
email against `coach_allowlist` in the kb Postgres. BOTH must pass — no token, bad token,
or an email not on the list ⇒ deny. An EMPTY/absent allowlist denies everyone (the door
stays shut when unconfigured). This is deliberately NOT the roadmap's domain helper, which
defaults empty→allow-all (fail-open). Per-email only.

Token issuance (the login flow that mints this JWT) is separate; the allowlist is edited
by Claude SQL (no UI). The CNAME must not go live until this gate is wired (an ungated
/api/chat blocks cutover by construction).
"""
from __future__ import annotations

import os

import jwt

COACH_JWT_SECRET = os.environ.get("COACH_JWT_SECRET", "")
COOKIE_NAME = "coach_session"


def session_payload(authorization: str | None, cookie_token: str | None) -> dict | None:
    """Verified JWT claims from a Bearer header or the coach_session cookie, else None."""
    if not COACH_JWT_SECRET:
        return None
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
    elif cookie_token:
        token = cookie_token
    if not token:
        return None
    try:
        return jwt.decode(token, COACH_JWT_SECRET, algorithms=["HS256"], options={"verify_aud": False})
    except jwt.InvalidTokenError:
        return None


def email_from_request(authorization: str | None, cookie_token: str | None) -> str | None:
    """Verified email from a Bearer header or the coach_session cookie, else None."""
    payload = session_payload(authorization, cookie_token)
    if not payload:
        return None
    email = payload.get("email")
    return email.strip().lower() if isinstance(email, str) and email.strip() else None


def is_allowed(email: str | None, conn) -> bool:
    """True iff `email` has a row in coach_allowlist. Empty/absent email ⇒ False (fail-closed)."""
    if not email:
        return False
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM coach_allowlist WHERE lower(email) = lower(%s)", (email,))
        return cur.fetchone() is not None
