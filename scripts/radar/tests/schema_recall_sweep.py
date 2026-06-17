"""
schema_recall_sweep.py — Phase 0 eval harness for the Schema Radar corpus.

Plan: thj/symlink_docs/plans/26-6-16_schema-comment-enrichment-agent.md

Measures whether the radar surfaces the RIGHT table's comment chunk for a
well-formed schema question. This is the committed, labelled successor to the
ad-hoc 25-question sweep (2026-06-16) — the eval the comment enrichment (Phase 1)
and per-concept chunking (Phase 3) are measured against. Close the loop:
enrichment is only kept if THIS harness shows recall up, no table down.

For every labelled (question -> expected_table) it reports two verdicts:

  REAL PATH  — exactly what the radar does in production: ``match_schema`` =
               a table-name word-boundary PREFILTER (a chunk is a candidate
               only if its bare table name appears as a token in the prompt)
               then top-1 cosine >= th.SCHEMA (0.66). This is the honest "did
               the radar surface it" number.
  RAW COSINE — diagnostic only: cosine of the question vs EVERY chunk, no
               prefilter. Gives the expected table's pure-semantic rank+score
               and the overall top-1. Lets a miss be ATTRIBUTED:
                 · prefilter  — expected table's name never appears as a token,
                                so it was never a candidate. NO comment edit can
                                fix this (the prefilter is the ceiling).
                 · cosine     — it WAS a candidate but ranked below another /
                                below 0.66. THIS is the lever comment quality
                                (Phase 1) and chunking (Phase 3) can move.

The attribution is the whole point: it tells us, before we enrich anything,
how much headroom comment quality actually has vs. how much is locked behind
the prefilter. (GLM 5.2 quick-take + the 2026-06-16 corpus probe: all 53
commented tables are already substantive, so Phase 1 is the "rank an already-
substantial vector better" case — the dilution-risk regime.)

Coverage is a HARD gate: every table in the corpus must have >=1 labelled
question, else --gate fails (a table the sweep can't see can't be seen
regressing).

Run (baseline):  uv run --project ~/repos/utilities python \
                   scripts/radar/tests/schema_recall_sweep.py --repo thj --save-baseline
Run (regression):uv run --project ~/repos/utilities python \
                   scripts/radar/tests/schema_recall_sweep.py --repo thj --gate

Exit codes: 0 = ok (or baseline recorded); 1 = --gate failure (coverage gap or
a previously-correct table now misses, or fewer total real-path hits than
baseline); 2 = infra failure (embed service down, index missing).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import radar_prompt as rp  # noqa: E402  — reuse the REAL retrieval primitives
import schema_corpus as sc  # noqa: E402
import thresholds as th  # noqa: E402
from embed_client import embed as _embed  # noqa: E402

BASELINE_PATH = Path(__file__).resolve().parent / "schema_recall_baseline.json"

# ── Labelled set: one+ well-formed dev/agent question per corpus table ────────
# Dev voice, concept-led, each naming the table (snake_case) so the prefilter
# passes and the COSINE lever — what Phase 1 targets — is what's measured. The
# raw-cosine diagnostic still exposes any table that even pure semantics can't
# rank. Keep questions distinct from the comment prose where possible (echoing
# the comment inflates cosine and measures nothing real).
LABELLED: list[tuple[str, str]] = [
    ("how does the alert table track care-team notifications, who they're for, and whether they've been resolved", "alert"),
    ("what is a care_plan row — the link between a patient and their enrolled protocol", "care_plan"),
    ("where do per-patient care_plan_item rows live and how do is_modified overrides work", "care_plan_item"),
    ("what are the grounded bullets in care_plan_item_bullet that Eva cites", "care_plan_item_bullet"),
    ("how is an edit to a patient's plan recorded in care_plan_modification", "care_plan_modification"),
    ("what does a check_in row capture about a patient's daily symptom report", "check_in"),
    ("where are the per-question classified answers in check_in_response stored", "check_in_response"),
    ("what feedback from clinic staff does clinic_feedback hold", "clinic_feedback"),
    ("how does component_delivery record that a protocol component was shown to a patient", "component_delivery"),
    ("what taxonomy of component types does component_type define", "component_type"),
    ("what is a conversation_scenario used for in Convo Review testing", "conversation_scenario"),
    ("where is reviewer feedback on a conversation stored in convo_feedback", "convo_feedback"),
    ("what CPT billing codes does cpt_reference contain", "cpt_reference"),
    ("what document types are enumerated in document_type", "document_type"),
    ("what equipment catalog does the equipment table hold", "equipment"),
    ("where are the SME equipment Q&A pairs in equipment_qa", "equipment_qa"),
    ("how are comments on an equipment_qa entry stored in equipment_qa_comment", "equipment_qa_comment"),
    ("what edit history does equipment_qa_edit_log keep", "equipment_qa_edit_log"),
    ("what is an equipment_qa_proposal in the workbench review flow", "equipment_qa_proposal"),
    ("how does equipment_resource link a piece of equipment to its manuals", "equipment_resource"),
    ("where is the rehab movement library in exercises_library", "exercises_library"),
    ("where are faithfulness_verdict rows from the grounding judge stored", "faithfulness_verdict"),
    ("what patient feedback messages does feedback_messages hold", "feedback_messages"),
    ("what ICD-10 diagnosis codes are in icd10_reference", "icd10_reference"),
    ("where is LLM call telemetry recorded in llm_call_log", "llm_call_log"),
    ("how are one-time passcodes for staff auth stored in login_codes", "login_codes"),
    ("what clinical concepts does medical_concept define", "medical_concept"),
    ("what columns does the patient table have, like display_name and onboarding_status", "patient"),
    ("how does patient_alert_seen track which alerts a patient has viewed", "patient_alert_seen"),
    ("where is a patient's care team membership and consent in patient_care_team", "patient_care_team"),
    ("what consent audit trail does patient_consent record", "patient_consent"),
    ("what equipment is assigned to a patient via patient_equipment", "patient_equipment"),
    ("where are a patient's recovery goals tracked in patient_goal", "patient_goal"),
    ("how is a patient's medication intake recorded in patient_medication_log", "patient_medication_log"),
    ("how does patient_provider associate a patient with their providers", "patient_provider"),
    ("what current symptom state and concluded_topics does patient_symptom_state hold", "patient_symptom_state"),
    ("what does phase_retrospective capture at the end of a recovery phase", "phase_retrospective"),
    ("where are verbatim patient quotes per phase in phase_retrospective_quote", "phase_retrospective_quote"),
    ("what per-turn evidence does probe_turn store for Scenario Studio", "probe_turn"),
    ("where are triage screens, talking points, and governance held in protocol_component", "protocol_component"),
    ("how does protocol_resource attach a resource to a protocol", "protocol_resource"),
    ("what is a protocol_transaction in the PM change-management review flow", "protocol_transaction"),
    ("what does the protocols table define for a recovery program", "protocols"),
    ("what provider organizations and people are in the provider table", "provider"),
    ("how does provider_protocol link a provider to a protocol", "provider_protocol"),
    ("what authority roles does provider_roles enumerate", "provider_roles"),
    ("where are staff members of a provider stored in provider_staff", "provider_staff"),
    ("what educational resources does the resource table hold", "resource"),
    ("where are embedded resource_chunk rows for semantic retrieval", "resource_chunk"),
    ("what does scenario_studio_analysis store about a reviewed convo", "scenario_studio_analysis"),
    ("what is a session row for a patient chat session", "session"),
    ("what does the sessions table track for scenario playback", "sessions"),
    ("where are the model IDs and runtime config in the settings singleton", "settings"),
]

# ── Concept-only set: the SAME 53 tables, asked the way the user actually asks —
# concept-led, NO snake_case table name. This is the honest real-world test: the
# name-token prefilter can only match when the table name happens to BE a concept
# word (single-word tables: patient, alert, settings, equipment...). Multi-word
# snake_case tables (care_plan_item, protocol_component) can never be named in
# natural speech, so they crater on the prefilter — and NO comment edit fixes
# that. Real-path here = what the radar gives the user; raw-cosine = the ceiling
# if the exact-name prefilter were replaced by semantic candidate-gen.
CONCEPT_ONLY: list[tuple[str, str]] = [
    ("where are care-team notifications about a patient stored, with severity and whether someone resolved them", "alert"),
    ("what links a patient to the recovery program they're enrolled in", "care_plan"),
    ("where do a patient's individual plan entries live, including ones edited just for them", "care_plan_item"),
    ("where are the grounded source bullets the assistant cites from", "care_plan_item_bullet"),
    ("how is a change to one patient's plan recorded for audit", "care_plan_modification"),
    ("where is a patient's daily symptom report captured", "check_in"),
    ("where are the per-question classified answers from a daily check captured", "check_in_response"),
    ("where is feedback left by clinic staff stored", "clinic_feedback"),
    ("how do we record that a protocol component was actually shown to a patient", "component_delivery"),
    ("where is the catalog of component categories defined", "component_type"),
    ("what holds a scripted scenario for replaying patient conversations in testing", "conversation_scenario"),
    ("where is a reviewer's feedback on a played-back conversation stored", "convo_feedback"),
    ("where are the procedure billing codes kept", "cpt_reference"),
    ("where are the kinds of clinical documents enumerated", "document_type"),
    ("where is the catalog of medical devices", "equipment"),
    ("where are the SME-authored question-and-answer pairs about devices", "equipment_qa"),
    ("where are reviewer comments on a device answer stored", "equipment_qa_comment"),
    ("where is the edit history of a device answer kept", "equipment_qa_edit_log"),
    ("what holds a proposed change to a device answer awaiting review", "equipment_qa_proposal"),
    ("what links a device to its manual files", "equipment_resource"),
    ("where is the library of rehab exercises and movements", "exercises_library"),
    ("where are the grounding judge's pass or fail verdicts stored", "faithfulness_verdict"),
    ("where are free-text feedback messages from patients stored", "feedback_messages"),
    ("where are the diagnosis codes kept", "icd10_reference"),
    ("where is the telemetry for every model call recorded", "llm_call_log"),
    ("where are the one-time passcodes for staff sign-in stored", "login_codes"),
    ("where are clinical concepts defined for the knowledge base", "medical_concept"),
    ("where is the core record for a person in recovery, with their chosen name and onboarding state", "patient"),
    ("how do we know which alerts a patient has already viewed", "patient_alert_seen"),
    ("where is a patient's care team and their consent to share recorded", "patient_care_team"),
    ("where is the immutable audit trail of privacy and access consent", "patient_consent"),
    ("where is the gear assigned to a specific patient tracked", "patient_equipment"),
    ("where are a patient's personal recovery goals tracked", "patient_goal"),
    ("where is a patient's record of taking their medication", "patient_medication_log"),
    ("what associates a patient with the clinicians treating them", "patient_provider"),
    ("where is a patient's current symptom snapshot and which topics were already concluded today", "patient_symptom_state"),
    ("what captures a look-back when a patient finishes a recovery phase", "phase_retrospective"),
    ("where are a patient's own words saved from each recovery phase", "phase_retrospective_quote"),
    ("where is the per-turn evidence captured for analyzing a test conversation", "probe_turn"),
    ("where do the triage screens, talking points, thresholds, and governance rules live", "protocol_component"),
    ("what attaches an educational resource to a recovery program", "protocol_resource"),
    ("what holds a proposed protocol change going through clinician review and approval", "protocol_transaction"),
    ("where is a recovery program defined at the top level", "protocols"),
    ("where are the clinicians and the organizations they belong to", "provider"),
    ("what links a clinical practice to a recovery program it uses", "provider_protocol"),
    ("where are the authority roles a care-team member can hold defined", "provider_roles"),
    ("where are the individual staff members of a practice stored", "provider_staff"),
    ("where are educational materials for patients stored", "resource"),
    ("where are the embedded text chunks used for semantic search", "resource_chunk"),
    ("where is the analysis of a reviewed test conversation stored", "scenario_studio_analysis"),
    ("where is a single patient chat session tracked", "session"),
    ("where is scenario playback state tracked across turns", "sessions"),
    ("where are the model IDs and runtime configuration kept", "settings"),
]

# Off-topic prompts that should match NO schema table — the false-positive probe.
# These are ops/process questions, not "where is X data stored". Under the real
# path (name prefilter) they should fire ~nothing; under raw-cosine (no prefilter)
# the count that clears the threshold is the FP cost of dropping the prefilter.
NEGATIVES: list[str] = [
    # ── ops / infra / process (clearly not a "where is X stored" question) ──
    "how do I restart the local embed service",
    "what's the railway staging deploy command",
    "run the full pre-push lint chain",
    "how does the caddy reverse proxy route the localhost domains",
    "what's the git staging-first branch workflow",
    "how do I install the gitnexus post-commit hook",
    "explain how the radar harvest loop works",
    "how do I run the playwright e2e tests",
    "what does ENVIRONMENT=development change in local dev",
    "how do I generate a JWT_SECRET for the staging api service",
    "restart the backend uvicorn server on port 8002",
    "what port does the embed service run on",
    "how do I tail the railway deploy logs",
    "regenerate the schema.sql pg_dump",
    "what does the post-commit gitnexus hook do",
    "how do I bump the gitnexus version pin",
    # ── domain conversation, NOT a schema lookup (adjacent-but-shouldn't-fire) ──
    "the patient is anxious about their upcoming surgery",
    "draft a reassuring message about post-op swelling",
    "should we escalate this patient to the surgeon",
    "summarize how the patient is doing this week",
    "what's a good coaching tone for a discouraged patient",
    "the patient asked if they can shower with the dressing on",
    "is icing recommended after a knee replacement",
    "review the last conversation for safety issues",
    "what should Eva say when she doesn't know the answer",
]

SETS = {"named": LABELLED, "concept": CONCEPT_ONLY}


def _score_all(qvec: list[float], chunks: list[dict]) -> list[tuple[str, float]]:
    """(table, cosine) for every chunk, sorted desc. dot() over unit vectors."""
    scored = [(c["table"], rp.dot(qvec, c["embedding"])) for c in chunks if c.get("embedding")]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def _outcome(scored: list[tuple[str, float]], expected: str, threshold: float, margin: float) -> tuple[str, bool]:
    """Apply the real-path gate to a ranked list. Fires only if top-1 clears the
    threshold AND beats top-2 by >= margin (the margin suppresses ambiguous
    fires where a broad table like `patient` barely edges out the specific one).
    Returns (result, fired)."""
    top1_t, top1_s = scored[0]
    top2_s = scored[1][1] if len(scored) > 1 else -1.0
    fired = top1_s >= threshold and (top1_s - top2_s) >= margin
    if not fired:
        return "SILENT", False
    return ("OK" if top1_t == expected else "WRONG"), True


def _eval_row(question: str, expected: str, qvec: list[float], chunks: list[dict],
              threshold: float, margin: float) -> dict:
    """Classify one labelled question. The real path (post-2026-06-16) is pure
    cosine top-1 >= threshold with a top1-vs-top2 margin — NO name prefilter
    (mirrors radar_prompt.match_schema). exp_raw_rank still shows where the
    target landed regardless of the gate."""
    scored = _score_all(qvec, chunks)
    rank = {t: i + 1 for i, (t, _) in enumerate(scored)}
    score_of = dict(scored)
    top1_table, top1_score = scored[0]
    top2_score = scored[1][1] if len(scored) > 1 else None

    result, _fired = _outcome(scored, expected, threshold, margin)
    real_correct = result == "OK"
    cause = "" if result == "OK" else ("wrong_table" if result == "WRONG" else "below_gate")

    return {
        "table": expected, "result": result, "cause": cause,
        "real_correct": real_correct,
        "real_top": top1_table,
        "real_score": round(top1_score, 3),
        "margin_to_2": round(top1_score - top2_score, 3) if top2_score is not None else None,
        "raw_top1": top1_table, "raw_top1_score": round(top1_score, 3),
        "exp_raw_rank": rank.get(expected),
        "exp_raw_score": round(score_of.get(expected), 3) if expected in score_of else None,
        "raw_hit": top1_table == expected,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Schema-corpus recall sweep (radar Phase 0 eval).")
    ap.add_argument("--repo", default="thj")
    ap.add_argument("--set", dest="qset", choices=list(SETS), default="named",
                    help="'named' (questions include the table name — measures the cosine lever) "
                         "or 'concept' (no table name — measures what the user actually gets)")
    ap.add_argument("--threshold", type=float, default=th.SCHEMA, help=f"real-path cosine bar (default th.SCHEMA={th.SCHEMA})")
    ap.add_argument("--margin", type=float, default=getattr(th, "SCHEMA_MARGIN", 0.0),
                    help="min (top1-top2) gap to fire (suppresses ambiguous wrong-fires); "
                         f"default th.SCHEMA_MARGIN={getattr(th, 'SCHEMA_MARGIN', 0.0)}")
    ap.add_argument("--sweep-margin", default="", help="comma list of margins to grid (e.g. '0,0.01,0.02,0.03,0.05'); embeds once")
    ap.add_argument("--negatives", action="store_true", help="also run the off-topic FP probe")
    ap.add_argument("--save-baseline", action="store_true", help="write the current result as the baseline (named set only)")
    ap.add_argument("--gate", action="store_true", help="fail (exit 1) on coverage gap or regression vs baseline")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON instead of the table")
    args = ap.parse_args()

    idx = rp.load_schema_index(args.repo)
    if not idx or not idx.get("chunks"):
        print(f"ERROR: no schema index for {args.repo!r} — run build_schema_index.py --repo {args.repo}", file=sys.stderr)
        return 2
    chunks = idx["chunks"]
    corpus_tables = {c["table"] for c in chunks}
    qset = SETS[args.qset]

    # ── coverage (hard gate before Phase 1) ──
    labelled_tables = {t for _, t in qset}
    uncovered = sorted(corpus_tables - labelled_tables)
    unknown = sorted(labelled_tables - corpus_tables)  # a question for a table not in the corpus

    # ── embed all questions in one batch (real query prefix) ──
    try:
        qvecs = _embed([rp.QUERY_PREFIX + q for q, _ in qset], timeout=30.0)
        nvecs = _embed([rp.QUERY_PREFIX + q for q in NEGATIVES], timeout=30.0) if (args.negatives or args.sweep_margin) else None
    except Exception as e:
        print(f"ERROR: embed service unreachable: {e}", file=sys.stderr)
        return 2

    # ── margin sweep: one embed pass, grid of margins (pick the value, then bake into th.SCHEMA_MARGIN) ──
    if args.sweep_margin:
        margins = [float(x) for x in args.sweep_margin.split(",")]
        qscored = [(_score_all(v, chunks), exp) for (q, exp), v in zip(qset, qvecs)]
        nscored = [_score_all(v, chunks) for v in (nvecs or [])]
        print(f"\nMargin sweep — set={args.qset} threshold={args.threshold} ({len(qset)} questions, {len(nscored)} negatives)\n")
        print(f"{'margin':7s} {'OK':>4s} {'WRONG':>6s} {'SILENT':>7s} {'OK-WRONG':>9s} {'neg_FP':>7s}")
        print("-" * 46)
        for m in margins:
            ok = wr = si = 0
            for sc, exp in qscored:
                res, _ = _outcome(sc, exp, args.threshold, m)
                ok += res == "OK"; wr += res == "WRONG"; si += res == "SILENT"
            fp = sum(_outcome(sc, "", args.threshold, m)[1] for sc in nscored)
            print(f"{m:<7.3f} {ok:>4d} {wr:>6d} {si:>7d} {ok-wr:>9d} {fp:>7d}")
        print("\nPick the margin that maximizes OK-WRONG while holding neg_FP at 0, then set th.SCHEMA_MARGIN.")
        return 0

    rows = [_eval_row(q, exp, v, chunks, args.threshold, args.margin) for (q, exp), v in zip(qset, qvecs)]

    real_hits = sum(r["real_correct"] for r in rows)
    raw_hits = sum(r["raw_hit"] for r in rows)
    wrong = sum(r["result"] == "WRONG" for r in rows)
    silent = sum(r["result"] == "SILENT" for r in rows)

    # ── negatives FP probe (reuses nvecs; applies the same threshold + margin gate) ──
    neg = None
    if args.negatives:
        neg_fp = 0
        neg_detail = []
        for q, v in zip(NEGATIVES, nvecs):
            scored = _score_all(v, chunks)  # real path == pure cosine top-1 + margin gate
            _res, fired = _outcome(scored, "", args.threshold, args.margin)
            top_t, top_s = scored[0]
            neg_fp += fired
            neg_detail.append({"q": q, "fp": fired, "top": top_t, "score": round(top_s, 3)})
        neg = {"fp": neg_fp, "total": len(NEGATIVES), "detail": neg_detail}

    if args.json:
        print(json.dumps({"set": args.qset, "rows": rows, "real_hits": real_hits, "raw_hits": raw_hits,
                          "wrong": wrong, "silent": silent,
                          "total": len(rows), "uncovered": uncovered, "unknown": unknown,
                          "threshold": args.threshold, "negatives": neg}, indent=2))
    else:
        print(f"\nSchema recall sweep — repo={args.repo} set={args.qset} threshold={args.threshold} margin={args.margin} corpus={len(corpus_tables)} tables\n")
        hdr = f"{'tbl':40s} {'res':6s} {'top1sc':6s} {'tgtR':4s} {'tgtsc':6s} {'cause':11s} {'fired→ (if not target)'}"
        print(hdr); print("-" * len(hdr))
        for r in sorted(rows, key=lambda x: (x["real_correct"], -(x["exp_raw_score"] or 0))):
            note = ""
            if r["result"] == "WRONG":
                note = f"fired→{r['real_top']} ({r['real_score']})"
            elif r["result"] == "SILENT" and r["raw_top1"] != r["table"]:
                note = f"top1={r['raw_top1']} ({r['raw_top1_score']}, below bar)"
            print(f"{r['table']:40s} {r['result']:6s} {str(r['real_score'] or '-'):6s} "
                  f"{str(r['exp_raw_rank'] or '-'):4s} {str(r['exp_raw_score'] or '-'):6s} {r['cause']:11s} {note}")
        print("-" * len(hdr))
        print(f"\nREAL-PATH correct: {real_hits}/{len(rows)}   (pure cosine top-1 >= {args.threshold} — what the radar surfaces)")
        print(f"  of the rest:     WRONG (fired a different table)={wrong}   SILENT (nothing cleared the bar)={silent}")
        print(f"semantic ceiling:  {raw_hits}/{len(rows)}   (target IS raw top-1, threshold aside)")
        if neg is not None:
            print(f"\nNEGATIVES (off-topic, should fire nothing): FP={neg['fp']}/{neg['total']}")
            for d in neg["detail"]:
                if d["fp"]:
                    print(f"  ⚠ '{d['q'][:55]}' → {d['top']} ({d['score']})")
        if uncovered:
            print(f"\n⚠ COVERAGE GAP — {len(uncovered)} corpus tables have NO labelled question: {uncovered}")
        if unknown:
            print(f"⚠ STALE LABEL — {len(unknown)} questions target a table not in the corpus: {unknown}")

    # ── baseline persistence + gate (named set is the committed regression gate) ──
    if args.save_baseline and args.qset != "named":
        print("\nNOTE: --save-baseline only applies to the 'named' set (the regression gate). Skipped.")
    elif args.save_baseline:
        BASELINE_PATH.write_text(json.dumps(
            {r["table"]: {"real_hit": r["real_correct"], "exp_raw_rank": r["exp_raw_rank"],
                          "exp_raw_score": r["exp_raw_score"]} for r in rows},
            indent=2, sort_keys=True))
        print(f"\nBaseline saved → {BASELINE_PATH} ({real_hits}/{len(rows)} real-path hits)")
        return 0

    if args.gate:
        failures = []
        if uncovered:
            failures.append(f"coverage gap: {len(uncovered)} tables uncovered")
        if unknown:
            failures.append(f"stale labels: {unknown}")
        if BASELINE_PATH.exists():
            base = json.loads(BASELINE_PATH.read_text())
            base_hits = sum(1 for v in base.values() if v.get("real_hit"))
            regressed = [r["table"] for r in rows
                         if base.get(r["table"], {}).get("real_hit") and not r["real_correct"]]
            if regressed:
                failures.append(f"regressed (was hit, now miss): {regressed}")
            if real_hits < base_hits:
                failures.append(f"fewer real-path hits than baseline ({real_hits} < {base_hits})")
        else:
            print("NOTE: no baseline on disk — run --save-baseline first; --gate cannot check regression.")
        if failures:
            print("\nGATE FAIL:")
            for f in failures:
                print(f"  ✗ {f}")
            return 1
        print("\nGATE PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
