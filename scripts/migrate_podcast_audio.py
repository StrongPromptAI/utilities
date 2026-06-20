#!/usr/bin/env python3
"""One-shot: migrate podcast audio off oxp.files (OrthoXpress client bucket) onto
the StrongPrompt podcast server's own Railway volume.

  oxp.files `Podcast/`   → podcast show `sales`  (/data/audio/sales/)
  oxp.files `briefings/`  → podcast show `tech`   (/data/audio/tech/)   [briefings retired]

For each file: presigned GET from oxp.files → PUT to the podcast upload endpoint
(service-token auth) → verify the server-reported byte count matches the source.
Deletion from oxp.files is a SEPARATE step (--delete), only after every file is
verified — a silent partial copy must never be followed by a delete.

Secrets are pulled live from Railway; nothing is written to disk.

  uv run --with requests --with pyjwt python scripts/migrate_podcast_audio.py            # migrate + verify
  uv run --with requests --with pyjwt python scripts/migrate_podcast_audio.py --delete   # + remove from oxp.files
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import jwt
import requests

RW = "https://backboard.railway.com/graphql/v2"
RW_TOKEN = json.load(open(os.path.expanduser("~/.config/keys.json")))["railway_main"]

OXP = dict(project="96a6d9dd-b680-4821-bee6-ed850a19074b",
           env="30bf77ef-ec92-472d-b92a-93e3806bd7e4",
           svc="56aebab1-320e-48d2-9053-44cacc82c241")
POD = dict(project="f4451750-12a8-4cff-9bc8-1796a9c15508",
           env="844d5562-ac1d-4a22-b249-986be610a0a5",
           svc="5a1fc29d-3556-4fd3-b039-dd9cb0d43ec7")

PODCAST_BASE = "https://podcast-production-31c9.up.railway.app"
OXP_FALLBACK = "https://oxp.files.strongprompt.ai"

# (oxp.files folder, podcast show slug)
MIGRATIONS = [("Podcast", "sales"), ("briefings", "tech")]


def rw_vars(t: dict) -> dict:
    q = (f'query {{ variables(projectId: "{t["project"]}", '
         f'environmentId: "{t["env"]}", serviceId: "{t["svc"]}") }}')
    r = requests.post(RW, headers={"Authorization": f"Bearer {RW_TOKEN}",
                                   "Content-Type": "application/json"},
                      json={"query": q}, timeout=30)
    return r.json()["data"]["variables"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--delete", action="store_true",
                    help="After all files verify, delete them from oxp.files.")
    args = ap.parse_args()

    print("pulling secrets from Railway…")
    oxp_v = rw_vars(OXP)
    oxp_secret = oxp_v["JWT_SECRET"]
    oxp_base = (oxp_v.get("PUBLIC_BASE_URL") or OXP_FALLBACK).rstrip("/")
    upload_secret = rw_vars(POD)["PODCAST_UPLOAD_SECRET"]

    now = int(time.time())
    oxp_tok = jwt.encode({"sub": "podcast-migrate", "iat": now, "exp": now + 1800},
                         oxp_secret, algorithm="HS256")
    up_tok = jwt.encode({"aud": "podcast-upload", "exp": now + 1800},
                        upload_secret, algorithm="HS256")
    oxp_hdr = {"Authorization": f"Bearer {oxp_tok}"}
    up_hdr = {"Authorization": f"Bearer {up_tok}"}

    def move_one(folder: str, slug: str, name: str, size: int) -> bool:
        """presign on oxp.files → tell the podcast service to PULL that URL itself
        (/import). Bytes never touch this client's slow uplink. Idempotent; retries.
        Returns True iff the server-reported byte count matches the source size."""
        last = ""
        for attempt in range(4):
            try:
                pr = requests.get(f"{oxp_base}/api/files/presign/{name}",
                                  params={"folder": folder}, headers=oxp_hdr, timeout=(15, 60))
                if pr.status_code != 200:
                    last = f"presign {pr.status_code}"; continue
                src_url = pr.json()["url"]
                ir = requests.post(f"{PODCAST_BASE}/import/{slug}/{name}",
                                   headers=up_hdr, json={"source_url": src_url}, timeout=(30, 600))
                if ir.status_code != 200:
                    last = f"import {ir.status_code} {ir.text[:80]}"; continue
                wrote = ir.json().get("bytes", -1)
                ok = wrote == size if size else wrote > 0
                print(f"  {'✓' if ok else '✗'} {name}: src={size:,} server={wrote:,}"
                      + ("" if ok else "  (byte mismatch)"))
                return ok
            except requests.RequestException as exc:
                last = type(exc).__name__
            if attempt < 3:
                time.sleep(2 * (attempt + 1))
        print(f"  ✗ {name}: failed after retries — {last}")
        return False

    migrated, failed = [], []
    for folder, slug in MIGRATIONS:
        print(f"\n=== {folder}/ → {slug} ===")
        lr = requests.get(f"{oxp_base}/api/files", params={"folder": folder},
                          headers=oxp_hdr, timeout=30)
        if lr.status_code != 200:
            print(f"  list failed {lr.status_code}: {lr.text[:120]}"); failed.append((folder, "LIST")); continue
        files = lr.json().get("files", [])
        if not files:
            print("  (empty)"); continue
        # smallest first — get the easy wins in before any large-file flakiness
        for f in sorted(files, key=lambda x: int(x.get("size", 0))):
            name, size = f["name"], int(f.get("size", 0))
            (migrated if move_one(folder, slug, name, size) else failed).append((slug, name))

    print(f"\nmigrated+verified: {len(migrated)} | failed: {len(failed)}")
    if failed:
        print("FAILURES:", failed); print("NOT deleting anything."); sys.exit(1)

    if args.delete:
        print("\n=== deleting verified files from oxp.files ===")
        for folder, slug in MIGRATIONS:
            for s, name in migrated:
                if s != slug:
                    continue
                dr = requests.delete(f"{oxp_base}/api/files/{name}",
                                     params={"folder": folder}, headers=oxp_hdr, timeout=30)
                print(f"  {folder}/{name}: {dr.status_code} {dr.json() if dr.status_code==200 else dr.text[:80]}")
    else:
        print("\n(verify-only; re-run with --delete to remove from oxp.files)")


if __name__ == "__main__":
    main()
