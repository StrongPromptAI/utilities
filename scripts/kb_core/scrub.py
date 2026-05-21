"""PHI scrubber — Presidio wrapper with stable token mapping.

Two entry points:

- `scrub(text, mapping=None)` — returns `(scrubbed_text, mapping)`. Pass an
  existing mapping to keep tokens stable across calls (same name → same
  token across chunks of one meeting).

- `rehydrate(text, mapping)` — reverses the scrub on LLM output.

The mapping is just `{token: original_value}` — caller's responsibility to
persist if needed across sessions.

Entity types recognized (Presidio defaults):
  PERSON, EMAIL_ADDRESS, PHONE_NUMBER, US_SSN, CREDIT_CARD, LOCATION,
  DATE_TIME, URL, IP_ADDRESS, IBAN_CODE, MEDICAL_LICENSE, US_DRIVER_LICENSE,
  NRP, US_BANK_NUMBER, US_PASSPORT, US_ITIN.

Custom recognizer: MRN (medical record number) — pattern `[A-Z]?-?\\d{6,10}`
appearing near "MRN" or "medical record" keywords. Tunable in `_build_engine`.
"""
from __future__ import annotations

import re
from typing import Tuple


_ANALYZER = None  # Lazy-init Presidio engines (heavy spaCy load)
_ANONYMIZER = None


def _build_engine():
    global _ANALYZER, _ANONYMIZER
    if _ANALYZER is not None:
        return _ANALYZER, _ANONYMIZER

    from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
    from presidio_anonymizer import AnonymizerEngine

    analyzer = AnalyzerEngine()

    # Custom MRN recognizer — Presidio doesn't ship one by default.
    mrn_pattern = Pattern(
        name="mrn_alphanumeric",
        regex=r"\b(?:MRN[:\s#-]*)?([A-Z]?-?\d{6,10})\b",
        score=0.6,
    )
    mrn_recognizer = PatternRecognizer(
        supported_entity="MRN",
        patterns=[mrn_pattern],
        context=["MRN", "medical record", "record number", "patient ID"],
    )
    analyzer.registry.add_recognizer(mrn_recognizer)

    _ANALYZER = analyzer
    _ANONYMIZER = AnonymizerEngine()
    return _ANALYZER, _ANONYMIZER


def _dedup_spans(results: list) -> list:
    """Drop overlapping spans, keeping the longer/higher-score one.

    Presidio happily emits overlapping matches (e.g., MRN and US_DRIVER_LICENSE
    both matching the same digit substring). Replacing both garbles the output
    because the second replacement lands inside the first's token.

    Strategy: sort by (start asc, length desc, score desc), then walk and drop
    any span whose start lies inside an already-kept span's [start, end).
    """
    sorted_results = sorted(results, key=lambda r: (r.start, -(r.end - r.start), -r.score))
    kept = []
    end_so_far = -1
    for r in sorted_results:
        if r.start >= end_so_far:
            kept.append(r)
            end_so_far = r.end
    return kept


def scrub(text: str, mapping: dict[str, str] | None = None) -> Tuple[str, dict[str, str]]:
    """De-identify `text`. Returns (scrubbed_text, mapping).

    Token shape: `[PERSON_1]`, `[EMAIL_ADDRESS_1]`, `[MRN_1]`, etc. Same source
    value gets the same token across calls when `mapping` is reused.
    """
    analyzer, _ = _build_engine()
    mapping = dict(mapping) if mapping else {}
    reverse = {v: k for k, v in mapping.items()}

    type_counters: dict[str, int] = {}
    for token in mapping:
        m = re.match(r"\[([A-Z_]+)_(\d+)\]", token)
        if m:
            etype, n = m.group(1), int(m.group(2))
            type_counters[etype] = max(type_counters.get(etype, 0), n)

    raw_results = analyzer.analyze(text=text, language="en")
    deduped = _dedup_spans(raw_results)
    # Now apply right-to-left so char offsets in `out` stay valid.
    deduped.sort(key=lambda r: r.start, reverse=True)

    out = text
    for r in deduped:
        original = text[r.start:r.end]
        if original in reverse:
            token = reverse[original]
        else:
            etype = r.entity_type
            type_counters[etype] = type_counters.get(etype, 0) + 1
            token = f"[{etype}_{type_counters[etype]}]"
            mapping[token] = original
            reverse[original] = token
        out = out[: r.start] + token + out[r.end:]

    return out, mapping


def rehydrate(text: str, mapping: dict[str, str]) -> str:
    """Replace tokens in `text` with their original values from `mapping`."""
    # Sort by length descending so `[PERSON_10]` is replaced before `[PERSON_1]`.
    for token in sorted(mapping, key=len, reverse=True):
        text = text.replace(token, mapping[token])
    return text
