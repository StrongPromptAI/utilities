#!/usr/bin/env python3
"""Ingest/refresh the roadmap chatbot's reference corpora (doctrine + stakeholder).

Drives kb_core's `ingest_reference_doc` over a fixed source-file list, with the
correct `reference_docs.category` per corpus. Upserts by (title, category) — existing
doctrine docs refresh in place, new ones insert — then prints the doc_id keep-set per
category so the caller can prune orphans and repopulate the `roadmap.settings`
allowlists (which mirror category membership).

Source of truth for the chatbot's RAG corpora. Re-run after editing any thj doctrine
or stakeholder doc. Title is derived from frontmatter `title:` or the first H1.

Usage:
  uv run python scripts/ingest_roadmap_corpus.py            # ingest
  uv run python scripts/ingest_roadmap_corpus.py --list     # print the file list, no ingest
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ingest_reference_doc import ingest_reference_doc

THJ = Path.home() / "repos/thj/symlink_docs"

DOCTRINE = [
    "project/BRANDING.md", "project/CARE_TEAM_MGMT.md",  # ALERT.md retired into CARE_TEAM_MGMT.md (2026-06-06)
    "project/CONVERSATION_DESIGN.md", "project/DATA_PROTECTION.md", "project/EQUIPMENT_CORPUS.md",
    "project/FIRST_100.md", "project/FRICTION.md", "project/MVP.md", "project/PRD.md",
    "project/PREHAB.md", "project/REVENUE.md", "project/VOICE_DESIGN.md",
]
STAKEHOLDER = [
    "stakeholders/dme-provider-sales.md", "stakeholders/dme-provider.md", "stakeholders/doctor.md",
    "stakeholders/investor.md", "stakeholders/patient.md", "stakeholders/physical-therapist.md",
]
PLAYBOOK = [
    # Authored THJ sales plays the chatbot coaches reps with. The 4 podcast
    # transcripts (docs 20-23) are roadmap `dme/transcripts/*` pages, maintained
    # and ingested as published roadmap content — not listed here.
    "sales-plays/pitching-doctors-on-thj.md",
]


def derive_title(text: str, fallback: str) -> str:
    m = re.search(r'^title:\s*["\']?(.+?)["\']?\s*$', text, re.MULTILINE)
    if m:
        return m.group(1).strip()
    m = re.search(r'^#\s+(.+)$', text, re.MULTILINE)
    if m:
        return m.group(1).strip()
    return fallback


def run(files: list[str], category: str) -> list[int]:
    keep: list[int] = []
    for rel in files:
        p = THJ / rel
        if not p.exists():
            print(f"  MISSING: {rel}")
            continue
        title = derive_title(p.read_text(), p.stem)
        r = ingest_reference_doc(str(p), category, title)
        if "error" in r:
            print(f"  ERROR {rel}: {r['error']}")
            continue
        keep.append(r["doc_id"])
        print(f"  [{category}] doc {r['doc_id']:>3}  {title[:42]:<42} {r['chunks_indexed']} chunks")
    return keep


if __name__ == "__main__":
    if "--list" in sys.argv:
        for rel in DOCTRINE:
            print(f"product_doctrine   {rel}")
        for rel in STAKEHOLDER:
            print(f"stakeholder_profile {rel}")
        sys.exit(0)

    print("=== DOCTRINE → product_doctrine ===")
    doctrine_ids = run(DOCTRINE, "product_doctrine")
    print("=== STAKEHOLDER → stakeholder_profile ===")
    stakeholder_ids = run(STAKEHOLDER, "stakeholder_profile")
    print("=== PLAYBOOK → sales_playbook ===")
    playbook_ids = run(PLAYBOOK, "sales_playbook")

    print(f"\nKEEP product_doctrine ids   ({len(doctrine_ids)}): {sorted(doctrine_ids)}")
    print(f"KEEP stakeholder_profile ids ({len(stakeholder_ids)}): {sorted(stakeholder_ids)}")
    print(f"KEEP sales_playbook ids     ({len(playbook_ids)}): {sorted(playbook_ids)} (+ the dme/transcripts podcast docs)")
    print("\nNext: prune product_doctrine docs NOT in the keep-set (orphans), then repopulate roadmap.settings allowlists from category.")
