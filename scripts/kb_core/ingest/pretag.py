"""Pre-tagging: speaker attribution, PII scrub, backend-required flag.

Phase 0 of the Q&A assembly pipeline. Prepares raw transcripts so every
downstream step (evidence, Q&A extraction, FAQ) gets clean, attributed input.

Data flow:
  raw_text (immutable, never modified)
      → regex pre-tag (fast, no LLM, ~70% accuracy)
      → LLM refinement (reads regex-tagged text, fixes misattributions)
      → PII scrub (redact DOB, phone, SSN)
      → tagged_text (derived, what everything downstream reads)

All LLM prompts use XML-structured hierarchy per prompt-architecture skill:
  system: <role_and_constraints> + <output_contract>
  user: <task_instruction> + <context> + <transcript>
"""

import json
import re
from openai import OpenAI
from ..config import LM_STUDIO_URL, SUMMARY_MODEL


# ============================================================
# REGEX PRE-TAGGING (fast first pass, no LLM)
# ============================================================

AGENT_PATTERNS = [
    r'(?i)thank you for calling',
    r'(?i)ortho\s*kinetics',
    r'(?i)how (?:can|may) I help',
    r'(?i)(?:this is|my name is)\s+{agent_first}',
    r'(?i)^{agent_first}\s+(?:speaking|here)',
    r'(?i)give me (?:one |a )?moment',
    r'(?i)let me (?:look|check|see|pull)',
    r'(?i)can I (?:have|get) your',
    r'(?i)(?:date of birth|spell (?:your|that)|what insurance|what is your|verify)',
    r'(?i)one moment,?\s*(?:okay|please)',
    r'(?i)I\'m going to (?:go ahead|send|transfer|email)',
]

CALLER_PATTERNS = [
    r'(?i)I\'m calling (?:about|because|from|on behalf)',
    r'(?i)my (?:doctor|surgeon|provider|orthopedic)',
    r'(?i)I (?:need|want|have|was told|ordered|received)',
    r'(?i)(?:when will|where is|how do I|can I|do you)',
    r'(?i)I\'m (?:a patient|checking|wondering|trying)',
    r'(?i)(?:my name is|this is)\s+(?!{agent_first})',
    r'(?i)(?:my (?:mom|dad|mother|father|son|daughter|husband|wife))',
    r'(?i)(?:surgery|knee|brace|walker|CPM|equipment|supplies)',
    r'(?i)I (?:haven\'t|still haven\'t|never) (?:received|heard|gotten)',
]


def _get_name_variants(agent_name: str) -> list[str]:
    """Get first name variants for fuzzy matching.

    Handles transcription spelling differences (Charon→Sharon, Jasmine→Jasma).
    """
    if not agent_name:
        return []
    first = agent_name.split()[0]
    variants = [first]
    if first.lower().startswith("ch"):
        variants.append(first[1:])
        variants.append("S" + first[2:])
    if first.lower().endswith("ine"):
        variants.append(first[:-3] + "a")
    if first.lower().endswith("elle"):
        variants.append(first[:-4] + "el")
    return variants


def _build_patterns(agent_name: str) -> tuple[list[re.Pattern], list[re.Pattern]]:
    """Compile regex patterns with agent's first name variants."""
    variants = _get_name_variants(agent_name)
    if variants:
        name_alt = "(?:" + "|".join(re.escape(v) for v in variants) + ")"
    else:
        name_alt = "NOMATCH_PLACEHOLDER"

    agent_compiled = []
    for p in AGENT_PATTERNS:
        try:
            agent_compiled.append(re.compile(p.replace('{agent_first}', name_alt)))
        except re.error:
            pass

    caller_compiled = []
    for p in CALLER_PATTERNS:
        try:
            caller_compiled.append(re.compile(p.replace('{agent_first}', name_alt)))
        except re.error:
            pass

    return agent_compiled, caller_compiled


def _score_segment(text: str, agent_patterns: list, caller_patterns: list) -> str:
    agent_score = sum(1 for p in agent_patterns if p.search(text))
    caller_score = sum(1 for p in caller_patterns if p.search(text))
    if agent_score > caller_score:
        return "[AGENT]"
    elif caller_score > agent_score:
        return "[CALLER]"
    return "[UNKNOWN]"


def _split_blob(text: str) -> list[str]:
    """Split a single-blob transcript into likely turn boundaries."""
    segments = re.split(r'(?<=[.!?])\s+(?=[A-Z])', text)
    merged = []
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        if merged and len(seg.split()) < 5:
            merged[-1] = merged[-1] + " " + seg
        else:
            merged.append(seg)
    return merged


def regex_pretag(raw_text: str, agent_name: str) -> str:
    """Fast regex-based speaker pre-tagging. No LLM. ~70% accuracy.

    This is the first pass — LLM refinement corrects the mistakes.
    """
    if not raw_text or not raw_text.strip():
        return raw_text or ""

    agent_patterns, caller_patterns = _build_patterns(agent_name or "")
    segments = _split_blob(raw_text)

    tagged = []
    prev_tag = None
    for seg in segments:
        tag = _score_segment(seg, agent_patterns, caller_patterns)
        if tag == "[UNKNOWN]" and prev_tag and len(seg.split()) < 8:
            tag = prev_tag
        tagged.append(f"{tag} {seg}")
        prev_tag = tag

    return "\n".join(tagged)


# ============================================================
# LLM SPEAKER REFINEMENT (corrects regex mistakes using context)
# ============================================================

REFINE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "speaker_refinement",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "tagged_transcript": {"type": "string"},
            },
            "required": ["tagged_transcript"],
        },
    },
}

REFINE_SYSTEM_MSG = """\
<role_and_constraints>
You refine speaker attribution in customer service call transcripts for a DME \
(Durable Medical Equipment) company called OrthoXpress / OrthoKinetics.

Rules:
- [AGENT] is the OrthoXpress employee who answers calls and looks up information.
- [CALLER] is the patient, family member, insurance rep, or provider calling in.
- Verification questions (date of birth, name spelling, insurance, address) are ALWAYS [AGENT].
- Answers to verification questions (providing DOB, spelling name, giving insurance info) are ALWAYS [CALLER].
- The agent's name is provided in context. If someone identifies themselves by that name, they are [AGENT].
- Fix any [UNKNOWN] tags using conversational flow.
- Do not change the text content — only fix the speaker tags.
</role_and_constraints>

<output_contract>
Return the full transcript with corrected [AGENT], [CALLER] tags on every line.
No [UNKNOWN] tags in the output — resolve them all.
</output_contract>"""


def refine_speaker_tags(regex_tagged: str, agent_name: str, category: str = None,
                        client: OpenAI = None) -> str:
    """LLM pass to correct regex pre-tagging mistakes.

    Reads the regex-tagged transcript and fixes misattributed speakers
    using conversational context.
    """
    if client is None:
        client = OpenAI(base_url=LM_STUDIO_URL, api_key="not-needed")

    text = regex_tagged
    if len(text) > 16000:
        text = text[:16000] + "\n[... truncated]"

    cat_line = f"\nCategory: {category}" if category else ""

    user_msg = f"""\
<task_instruction>
Review the speaker tags below. Fix any that are wrong based on conversational \
context. Resolve all [UNKNOWN] tags. Return the corrected tagged transcript.
</task_instruction>

<context>
Agent name: {agent_name}{cat_line}
</context>

<transcript>
{text}
</transcript>"""

    try:
        response = client.chat.completions.create(
            model=SUMMARY_MODEL,
            messages=[
                {"role": "system", "content": REFINE_SYSTEM_MSG},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=4096,
            temperature=0.1,
            response_format=REFINE_SCHEMA,
        )
        result = json.loads(response.choices[0].message.content)
        return result.get("tagged_transcript", regex_tagged)
    except Exception as e:
        print(f"  Warning: LLM refinement failed ({e}), using regex tags")
        return regex_tagged


# ============================================================
# PII LIGHT SCRUB
# ============================================================

PII_PATTERNS = [
    (r'\b(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})\b', '[DOB]'),
    (r'\b(\d{3}[-.\s]?\d{3}[-.\s]?\d{4})\b', '[PHONE]'),
    (r'\b(\d{3}-\d{2}-\d{4})\b', '[SSN]'),
]


def scrub_pii(text: str) -> str:
    """Light PII scrub — redact DOB, phone, SSN patterns."""
    result = text
    for pattern, replacement in PII_PATTERNS:
        result = re.sub(pattern, replacement, result)
    return result


# ============================================================
# BACKEND-REQUIRED CLASSIFICATION (XML-structured prompt)
# ============================================================

BACKEND_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "backend_check",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "backend_required": {"type": "boolean"},
                "reasoning": {"type": "string"},
            },
            "required": ["backend_required", "reasoning"],
        },
    },
}

BACKEND_SYSTEM_MSG = """\
<role_and_constraints>
You analyze customer service call transcripts for a DME (Durable Medical Equipment) \
company. Determine whether the caller's questions could have been FULLY answered \
using only static FAQ knowledge, or whether the call required looking up specific \
patient/order data in a backend system.

If the call had BOTH types (some static, some backend), answer true — backend was \
required for at least part of the call.
</role_and_constraints>

<definitions>
Static-answerable (backend_required=false):
- "How does the process work?" "What do you need from me?" "Do you carry walkers?"
- "Are you a walk-in store?" "What insurance do you accept?" "How long does delivery take?"
- General equipment questions: "How do I adjust my walker height?"

Backend-required (backend_required=true):
- "What date will MY supplies arrive?" "Has MY referral been processed?"
- "What is the status of MY specific order?" "Can you look up MY account?"
- Agent had to look up patient by name/DOB to answer the question
- Agent checked order system, auth system, or scheduling system to respond
</definitions>"""


def classify_backend_required(tagged_text: str, agent_name: str = None,
                              category: str = None, client: OpenAI = None) -> dict:
    """Classify whether a call required backend system access.

    Uses XML-structured prompt for clear separation of instructions and data.
    Returns {"backend_required": bool, "reasoning": str}.
    """
    if client is None:
        client = OpenAI(base_url=LM_STUDIO_URL, api_key="not-needed")

    text = tagged_text
    if len(text) > 16000:
        text = text[:16000] + "\n[... truncated]"

    cat_attr = f' category="{category}"' if category else ""
    agent_attr = f' agent_name="{agent_name}"' if agent_name else ""

    user_msg = f"""\
<task_instruction>
Analyze the call below. Could ALL of the caller's questions have been answered \
from static FAQ knowledge alone, or did the agent need to access a backend system \
(order lookup, authorization check, patient account, scheduling)?
</task_instruction>

<transcript{agent_attr}{cat_attr}>
{text}
</transcript>"""

    try:
        response = client.chat.completions.create(
            model=SUMMARY_MODEL,
            messages=[
                {"role": "system", "content": BACKEND_SYSTEM_MSG},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=200,
            temperature=0.1,
            response_format=BACKEND_SCHEMA,
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        return {"backend_required": None, "reasoning": f"Error: {e}"}


# ============================================================
# FULL PHASE 0 PIPELINE
# ============================================================

def pretag_diarized(raw_text: str, agent_name: str) -> str:
    """Tag a diarized transcript where raw_text has [Speaker N] prefixes.

    Identifies which Speaker N is the agent (by matching agent name or greeting
    patterns in their first few lines), then maps all lines deterministically.
    No LLM needed — speaker IDs are consistent within a call.
    """
    lines = raw_text.strip().split("\n")
    if not lines:
        return raw_text

    # Extract speaker IDs and their first few lines
    speaker_lines: dict[str, list[str]] = {}
    for line in lines:
        m = re.match(r'\[([^\]]+)\]\s*(.*)', line)
        if m:
            spk, text = m.group(1), m.group(2)
            speaker_lines.setdefault(spk, []).append(text)

    if not speaker_lines:
        # No speaker tags found — fall back to regex pretag
        return regex_pretag(raw_text, agent_name)

    # Determine which speaker is the agent
    name_variants = _get_name_variants(agent_name)
    agent_patterns_simple = [
        r'(?i)thank you for calling',
        r'(?i)ortho\s*kinetics',
        r'(?i)how (?:can|may) I help',
    ]
    # Add agent name variants
    for v in name_variants:
        agent_patterns_simple.append(rf'(?i)\b{re.escape(v)}\b')

    agent_speaker = None
    best_score = 0
    for spk, texts in speaker_lines.items():
        # Score first 5 lines from this speaker
        score = 0
        for text in texts[:5]:
            for pat in agent_patterns_simple:
                if re.search(pat, text):
                    score += 1
        if score > best_score:
            best_score = score
            agent_speaker = spk

    # Map speakers
    tagged_lines = []
    for line in lines:
        m = re.match(r'\[([^\]]+)\]\s*(.*)', line)
        if m:
            spk, text = m.group(1), m.group(2)
            if spk == agent_speaker:
                tagged_lines.append(f"[AGENT] {text}")
            else:
                tagged_lines.append(f"[CALLER] {text}")
        else:
            tagged_lines.append(f"[UNKNOWN] {line}")

    return "\n".join(tagged_lines)


def pretag_and_scrub(raw_text: str, agent_name: str, category: str = None,
                     client: OpenAI = None) -> str:
    """Full Phase 0 text pipeline. Produces derived tagged_text from immutable raw_text.

    For diarized transcripts ([Speaker N] prefixes): deterministic mapping, no LLM.
    For single-blob transcripts: regex pretag → LLM refinement.
    Both paths end with PII scrub.
    """
    # Detect diarized format
    if re.search(r'\[Speaker \d+\]', raw_text):
        tagged = pretag_diarized(raw_text, agent_name)
    else:
        # Legacy single-blob: regex → LLM refinement
        regex_tagged = regex_pretag(raw_text, agent_name)
        tagged = refine_speaker_tags(regex_tagged, agent_name, category, client)

    scrubbed = scrub_pii(tagged)
    return scrubbed
