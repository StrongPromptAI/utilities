"""Smoke-test meeting summary against a real Dialpad CSV.

Validates the model picks from plan 26-5-21 before committing the migration:
  primary: google/gemini-3.5-flash
  backup:  anthropic/claude-opus-4.7

No DB writes, no outline input — pure "given a transcript, can the model
produce themes + supporting detail + verbatim quotes?" check.

Usage:
  uv run python scripts/smoketest_summary.py [csv_path]
  uv run python scripts/smoketest_summary.py [csv_path] --model backup
  uv run python scripts/smoketest_summary.py [csv_path] --both    # run both for comparison
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

from openai import OpenAI


DEFAULT_CSV = Path.home() / "Downloads" / "brettchristiealmao.csv"
PRIMARY_MODEL = "google/gemini-3.5-flash"
BACKUP_MODEL = "anthropic/claude-opus-4.7"

PROMPT_TEMPLATE = """You are a meeting note-taker producing a high-quality summary of a real business meeting.

Produce a markdown summary with this structure:

## Participants
Brief one-line per participant — role/context if inferable from the conversation.

## Themes
For each cross-cutting theme that spans multiple parts of the meeting:

### <Theme name>
- Concrete supporting bullet (a commitment, number, name, decision, or specific detail)
- Another supporting bullet
- [Speaker Name: "verbatim quote from the transcript"]

Rules:
- Themes must capture cross-meeting patterns — NOT chunk-local "first they talked about X, then Y" summaries.
- Supporting bullets must preserve concrete detail: numbers, names, commitments, dates, decisions, blockers.
- Quotes must be VERBATIM from the transcript. Do not paraphrase. Use 1-3 per theme, picking the most load-bearing or revealing lines.
- Speaker attribution on quotes must match the transcript exactly.
- No invented content. If something is unclear or contradictory, omit it rather than guess.
- Aim for 3-7 themes total. Fewer is fine if the meeting was tightly focused.

## Open threads
Bullet list of anything left unresolved — questions raised but not answered, follow-ups mentioned but not committed, ambiguity that a participant would want to clarify.

---

Transcript follows. Lines are formatted as `Speaker Name: utterance`.

{transcript}
"""


def load_transcript(path: Path) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    with open(path, newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header and header[1].strip().lower() == "name" and header[2].strip().lower() == "content":
            pass
        else:
            if header and len(header) >= 3:
                s, t = header[1].strip(), header[2].strip()
                if s and t:
                    rows.append((s, t))
        for row in reader:
            if len(row) >= 3:
                speaker = row[1].strip()
                text = row[2].strip()
                if speaker and text:
                    rows.append((speaker, text))
    return rows


def build_prompt(rows: list[tuple[str, str]]) -> str:
    transcript = "\n".join(f"{s}: {t}" for s, t in rows)
    return PROMPT_TEMPLATE.format(transcript=transcript)


def call_model(model_id: str, prompt: str, api_key: str) -> tuple[str, dict, float]:
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)
    t0 = time.time()
    resp = client.chat.completions.create(
        model=model_id,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=8000,
        temperature=0.3,
    )
    elapsed = time.time() - t0
    usage = {
        "prompt_tokens": resp.usage.prompt_tokens,
        "completion_tokens": resp.usage.completion_tokens,
        "total_tokens": resp.usage.total_tokens,
    }
    return resp.choices[0].message.content, usage, elapsed


def cost_estimate(model_id: str, usage: dict) -> float:
    rates = {
        PRIMARY_MODEL: (1.50, 9.00),
        BACKUP_MODEL: (5.00, 25.00),
    }
    if model_id not in rates:
        return 0.0
    in_rate, out_rate = rates[model_id]
    return (usage["prompt_tokens"] * in_rate + usage["completion_tokens"] * out_rate) / 1_000_000


def run_one(model_id: str, prompt: str, api_key: str) -> str | None:
    print(f"\n=== {model_id} ===", file=sys.stderr)
    try:
        content, usage, elapsed = call_model(model_id, prompt, api_key)
    except Exception as e:
        print(f"FAIL: {type(e).__name__}: {e}", file=sys.stderr)
        return None
    cost = cost_estimate(model_id, usage)
    print(
        f"OK: {usage['prompt_tokens']} in, {usage['completion_tokens']} out, "
        f"{elapsed:.1f}s, ~${cost:.4f}",
        file=sys.stderr,
    )
    return content


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path", nargs="?", default=str(DEFAULT_CSV))
    parser.add_argument("--model", choices=["primary", "backup"], default="primary")
    parser.add_argument("--both", action="store_true", help="Run both models for side-by-side comparison")
    args = parser.parse_args()

    csv_path = Path(args.csv_path).expanduser()
    if not csv_path.exists():
        sys.exit(f"CSV not found: {csv_path}")

    api_key = json.load(open(os.path.expanduser("~/.config/keys.json")))["openrouter"]

    rows = load_transcript(csv_path)
    participants = sorted({s for s, _ in rows})
    prompt = build_prompt(rows)
    print(f"CSV: {csv_path}", file=sys.stderr)
    print(f"Turns: {len(rows)}  Participants: {', '.join(participants)}", file=sys.stderr)
    print(f"Prompt size: {len(prompt)} chars (~{len(prompt) // 4} tokens)", file=sys.stderr)

    if args.both:
        for model in (PRIMARY_MODEL, BACKUP_MODEL):
            content = run_one(model, prompt, api_key)
            print(f"\n\n# === Summary from {model} ===\n")
            print(content or "(failed)")
        return 0

    chosen = PRIMARY_MODEL if args.model == "primary" else BACKUP_MODEL
    fallback = BACKUP_MODEL if args.model == "primary" else PRIMARY_MODEL

    content = run_one(chosen, prompt, api_key)
    if content is None:
        print(f"\nFalling back to {fallback}", file=sys.stderr)
        content = run_one(fallback, prompt, api_key)
        if content is None:
            sys.exit("Both models failed.")
        print(f"\n# Summary — produced by {fallback} (fallback)\n")
    else:
        print(f"\n# Summary — produced by {chosen}\n")

    print(content)
    return 0


if __name__ == "__main__":
    sys.exit(main())
