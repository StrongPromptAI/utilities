#!/usr/bin/env python3
"""Mint a coach_session JWT (magic-link login) for an allowlisted email.

Interim login issuance until OTP/oidc is wired: the operator mints a token per
allowlisted person and shares the magic link. The coach's /auth route validates it and
sets the cookie. Run from the utilities repo root.

  COACH_JWT_SECRET=<prod secret> uv run python services/coach/mint_coach_token.py someone@orthoxpress.com
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))   # kb_core

import jwt


def main() -> int:
    ap = argparse.ArgumentParser(description="Mint a coach_session JWT for an allowlisted email.")
    ap.add_argument("email")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--base", default="https://coach.strongprompt.ai")
    args = ap.parse_args()

    secret = os.environ.get("COACH_JWT_SECRET")
    if not secret:
        raise SystemExit("FAIL-FAST: COACH_JWT_SECRET not set (must match the deployed coach's secret)")

    email = args.email.strip().lower()
    from kb_core import get_db
    with get_db() as c, c.cursor() as cur:
        cur.execute("SELECT 1 FROM coach_allowlist WHERE lower(email) = lower(%s)", (email,))
        if not cur.fetchone():
            raise SystemExit(f"{email} is not on coach_allowlist — add it first (INSERT), then mint")

    now = int(time.time())
    token = jwt.encode({"email": email, "iat": now, "exp": now + args.days * 86400}, secret, algorithm="HS256")
    print(f"email:      {email}")
    print(f"expires:    {args.days} days")
    print(f"magic link: {args.base}/auth?token={token}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
