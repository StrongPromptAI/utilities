#!/usr/bin/env python3
"""
meeting_prep (CLI) — reputation-strain read for a doctor/practice, from public reviews.

The goal is NOT "scrape Google reviews." It answers ONE question that maps to THJ's
value: how much is this practice's reputation propped up by manual staff touchpoints,
and how close is that model to the volume ceiling where more cases mean review erosion?
A high rating maintained by personal callbacks and hand-holding is a liability with a
ceiling — to grow, the surgeon adds overhead (crushing in California) or lets service
slip and erodes the score he fears losing. THJ automates those touchpoints: protect the
score AND lift the ceiling. So reviews are mined as evidence for that diagnosis.

This CLI is the standalone/dev surface. The load-bearing engine (search → extract →
citation-validation) is SHARED with the deployed sales coach and lives ONCE at
  symlink_docs/plans/hj_roadmap/app/backend/meeting_prep.py
which this file imports by path. The CLI adds the strain-verdict + opener synthesis
(grounded in the local doctor profile); the coach does that synthesis itself from the
same engine's facts + the profile it retrieves via RAG.

Output:
  1. Google rating + review count (Healthgrades too, when easy).
  2. A STRAIN VERDICT — touchpoint-dependence, erosion, ceiling-proximity, incumbent
     tech, and a "Gets it" read (per the doctor profile's segmentation).
  3. The cited EVIDENCE behind it — review snippets across five thesis categories,
     EACH carrying its real source URL.
  4. A 2–3 line OPENER running the frame (protect the scores while unlocking throughput).

Usage:
  uv run python scripts/meeting_prep.py "Dr. John Andrawis" --city "Torrance"
  uv run python scripts/meeting_prep.py "Dr. John Andrawis" --json        # facts only
  uv run python scripts/meeting_prep.py "Dr. John Andrawis" --search-model x-ai/grok-4.20-multi-agent
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
from pathlib import Path

# ── Import the ONE shared engine by path (it lives in the deployable backend tree) ──

_ENGINE_PATH = (Path(__file__).resolve().parent.parent
                / "symlink_docs/plans/hj_roadmap/app/backend/meeting_prep.py")
if not _ENGINE_PATH.exists():
    _ENGINE_PATH = Path("~/repo_docs/utilities/plans/hj_roadmap/app/backend/meeting_prep.py").expanduser()


def _load_engine():
    if not _ENGINE_PATH.exists():
        sys.stderr.write(f"❌ Shared engine not found at {_ENGINE_PATH}\n")
        sys.exit(1)
    spec = importlib.util.spec_from_file_location("mp_engine", _ENGINE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mp = _load_engine()

DEFAULT_SYNTH_MODEL = "anthropic/claude-sonnet-4.6"   # strain verdict + opener (judgment tier)
DOCTOR_PROFILE = Path("~/repo_docs/thj/stakeholders/doctor.md").expanduser()


def _log(msg: str) -> None:
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


def _die(msg: str) -> "None":
    _log(f"❌ {msg}")
    sys.exit(1)


# ── Synthesis: the strain verdict + opener, grounded in the doctor profile ───────
# Judgment, not extraction → the stronger tier. CLI-surface-specific (the coach does
# its own equivalent synth from the engine facts + RAG-retrieved profile).

_SYNTH_SCHEMA = {
    "name": "strain_read",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "touchpoint_dependence": {"type": "string", "enum": ["high", "medium", "low", "unknown"]},
            "touchpoint_rationale": {"type": "string", "description": "One short sentence citing the evidence."},
            "erosion": {"type": "string", "enum": ["present", "early", "absent", "unknown"]},
            "erosion_rationale": {"type": "string"},
            "ceiling_proximity": {"type": "string", "enum": ["near", "moderate", "far", "unknown"]},
            "incumbent_tech": {"type": ["string", "null"], "description": "Named patient app/portal, or null if none found."},
            "gets_it": {"type": "string", "enum": ["likely", "neutral", "unlikely", "unknown"]},
            "gets_it_rationale": {"type": "string"},
            "opener": {"type": "string", "description": "2–3 line opener the rep can say."},
        },
        "required": ["touchpoint_dependence", "touchpoint_rationale", "erosion", "erosion_rationale",
                     "ceiling_proximity", "incumbent_tech", "gets_it", "gets_it_rationale", "opener"],
    },
}


async def _synthesize(insights: dict, *, model: str, api_key: str, profile_text: str) -> tuple[dict, float]:
    sig_lines = "\n".join(
        f"- ({s['theme']} · {s.get('sentiment', '?')}) “{s['quote']}”" for s in insights["signals"]
    ) or "(no cited signals)"
    rating = insights.get("rating") or "unknown"
    count = insights.get("review_count") or "unknown"
    system = (
        "You are a sales strategist for a DME rep meeting an orthopedic surgeon. THESIS: a high "
        "rating maintained by manual staff touchpoints is a liability with a volume ceiling — to "
        "grow, the surgeon must add overhead (already crushing in California) or let service slip "
        "and erode the rating he fears losing. THJ automates those touchpoints: protect the score "
        "AND lift the ceiling.\n\n"
        "From the cited review signals + Google rating + the buyer profile, assess:\n"
        "- touchpoint_dependence: how much the reputation rests on manual human effort "
        "(praise crediting personal attention ⇒ high).\n"
        "- erosion: whether communication / throughput cracks are already showing.\n"
        "- ceiling_proximity: how close the practice is to where more volume erodes service.\n"
        "- incumbent_tech: any named app/portal, else null.\n"
        "- gets_it: early-adopter likelihood. Apply the profile's GENERAL 'Gets it / doesn't get it "
        "yet' signal criteria (tracks outcomes, talks prehab/recovery, treats readmissions as a "
        "business problem, invests in care coordination) to THIS practice's evidence — you do NOT "
        "need a profile specific to this surgeon. Use 'unknown' only when the reviews give no "
        "relevant signal either way.\n"
        "Each rationale is ONE short sentence citing the evidence. Then write `opener`: a 2–3 line "
        "opener the rep can say, running the frame — protect the scores while unlocking throughput — "
        "grounded in the surgeon's economics (the ~70% phone-call tax / staff cost, post-op risk as "
        "the real margin under bundled payments, rating sensitivity). If the signals are mostly "
        "positive, open on the hidden cost of sustaining that rating; if cracks show, name them. "
        "Honest, no fake precision, no invented numbers, never a price."
    )
    user = (
        f"PRACTICE: {insights['practice']}\nGOOGLE: {rating} stars · {count} reviews\n\n"
        f"REVIEW SIGNALS:\n{sig_lines}\n\nBUYER PROFILE (doctor):\n{profile_text}"
    )
    msg, cost = await mp._chat(
        model,
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        api_key=api_key, max_tokens=900, temperature=0.4, json_schema=_SYNTH_SCHEMA, timeout=60.0,
    )
    try:
        parsed = json.loads(msg.get("content") or "{}")
    except ValueError:
        _die(f"Synthesis model '{model}' did not return valid JSON.")
    return parsed, cost


# Order evidence by thesis priority: the cracks first, then the labor-cost-in-praise.
_THEME_ORDER = {
    "communication_crack": 0, "postop_uncertainty": 1, "throughput_strain": 2,
    "touchpoint_dependence": 3, "incumbent_tech": 4, "other": 5,
}
_THEME_MARK = {
    "communication_crack": "⚠️", "postop_uncertainty": "⚠️", "throughput_strain": "⚠️",
    "touchpoint_dependence": "🔧", "incumbent_tech": "🤖", "other": "•",
}


def _print_readout(insights: dict, verdict: dict | None) -> None:
    p = insights
    print(f"\n# Meeting prep — {p['practice']}" + (f" ({p['city']})" if p.get("city") else ""))
    rating = p.get("rating") or "—"
    count = p.get("review_count") or "—"
    line = f"\n**Google:** {rating} stars · {count} reviews"
    if p.get("healthgrades_rating"):
        line += f"  ·  **Healthgrades:** {p['healthgrades_rating']}"
    print(line)

    if verdict:
        print("\n## Reputation strain")
        print(f"- **Touchpoint-dependence:** {verdict['touchpoint_dependence'].upper()} — {verdict['touchpoint_rationale']}")
        print(f"- **Erosion signals:** {verdict['erosion'].upper()} — {verdict['erosion_rationale']}")
        print(f"- **Ceiling proximity:** {verdict['ceiling_proximity'].upper()}")
        print(f"- **Incumbent tech:** {verdict.get('incumbent_tech') or 'none found'}")
        print(f"- **“Gets it”:** {verdict['gets_it'].upper()} — {verdict['gets_it_rationale']}")

    signals = sorted(p["signals"], key=lambda s: _THEME_ORDER.get(s.get("theme"), 9))
    print(f"\n## Evidence ({len(signals)} cited)")
    if not signals:
        print("_Insufficient public review signal found — report what you can verify, don't fill the gap._")
    for s in signals:
        mark = _THEME_MARK.get(s.get("theme"), "•")
        print(f"\n- {mark} **[{s['theme']} · {s.get('sentiment', '?')}]** “{s['quote']}”\n  ↳ {s['source_url']}")

    if verdict:
        print("\n## Opener")
        print(verdict["opener"])

    print(f"\n_— estimated; mined from public reviews. cost ≈ ${p['cost_usd']:.4f}._")


async def _run(args) -> int:
    try:
        key = mp._openrouter_key()
    except RuntimeError as exc:
        _die(str(exc))

    _log(f"🔎 searching public reviews for “{args.practice}”{f' in {args.city}' if args.city else ''} "
         f"via {args.search_model} …")
    try:
        insights = await mp.fetch_review_insights(
            args.practice, args.city,
            search_model=args.search_model, extract_model=args.extract_model, api_key=key,
        )
    except (RuntimeError, ValueError) as exc:
        _die(str(exc))
    if insights["dropped_signals"]:
        _log(f"   dropped {insights['dropped_signals']} signal(s) with no resolvable source URL.")

    if args.json:
        print(json.dumps(insights, indent=2, ensure_ascii=False))
        return 0

    verdict = None
    if not args.no_synth and insights["signals"]:
        if DOCTOR_PROFILE.exists():
            _log(f"🧭 reading the strain + writing the opener via {args.synth_model} …")
            verdict, c3 = await _synthesize(
                insights, model=args.synth_model, api_key=key, profile_text=DOCTOR_PROFILE.read_text(),
            )
            insights["cost_usd"] = round(insights["cost_usd"] + c3, 4)
        else:
            _log(f"⚠️  {DOCTOR_PROFILE} not found — skipping verdict/opener (the facts above still stand).")

    _print_readout(insights, verdict)
    return 0 if insights["signals"] else 2  # fail-closed: no cited signals is not a readout


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Public reviews → reputation-strain verdict + opener for a doctor/practice. "
                    "Answers: how much is this reputation propped up by manual staff touchpoints, "
                    "and how close to the volume ceiling?",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("practice", help='Practice or doctor name, e.g. "Dr. John Andrawis".')
    ap.add_argument("--city", default=None, help="City to disambiguate the practice.")
    ap.add_argument("--search-model", default=mp.DEFAULT_SEARCH_MODEL,
                    help=f"Web-grounded search model (default: {mp.DEFAULT_SEARCH_MODEL}).")
    ap.add_argument("--extract-model", default=mp.DEFAULT_EXTRACT_MODEL,
                    help=f"Signal-extraction model (default: {mp.DEFAULT_EXTRACT_MODEL}).")
    ap.add_argument("--synth-model", default=DEFAULT_SYNTH_MODEL,
                    help=f"Strain-verdict + opener model (default: {DEFAULT_SYNTH_MODEL}).")
    ap.add_argument("--json", action="store_true", help="Print the raw facts as JSON (no verdict/opener).")
    ap.add_argument("--no-synth", action="store_true", help="Skip the strain verdict + opener (facts only).")
    args = ap.parse_args()
    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
