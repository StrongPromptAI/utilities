"""Transcript preprocessing functions."""

import re
import csv
from io import StringIO
from openai import OpenAI
from .config import LM_STUDIO_URL


def preprocess_dialpad_transcript(raw_text: str, merge_speaker_turns: bool = True, filter_fillers: bool = True, llm_adjudicate: bool = True) -> dict:
    """Preprocess Dialpad CSV transcript.

    Strips timestamps, preserves speaker attribution, optionally merges
    consecutive turns by the same speaker, filters out agreement fillers.

    Args:
        raw_text: Raw CSV content from Dialpad
        merge_speaker_turns: If True, merge consecutive lines from same speaker
        filter_fillers: If True, remove low-value agreement statements
        llm_adjudicate: If True, use local LLM to classify borderline cases

    Returns:
        {
            "text": cleaned transcript text,
            "participants": list of unique speakers,
            "turn_count": number of speaker turns,
            "filtered_count": number of filler turns removed,
            "llm_filtered_count": number filtered by LLM adjudication
        }
    """
    # Filler patterns - obvious agreement/acknowledgment with no semantic value
    OBVIOUS_FILLER_PATTERNS = [
        r'^(yup|yep|yeah|yes|okay|ok|right|sure|uh-huh|uh huh|mm-hmm|mm hmm|mmm|hmm|alright|got it|correct|true|exactly|absolutely|definitely|totally|i see|oh|ah)\.?!?$',
        r'^(yup|yep|yeah|yes|okay|ok|right|sure|alright)[,\s]+(yup|yep|yeah|yes|okay|ok|right|sure|alright)?\.?$',  # "yeah, yeah"
        r'^(oh|ah|hey)[,\.]?$',  # Just "oh" or "hey"
        r'^that\'s (right|correct|true|it)\.?$',
        r'^(sounds good|for sure|of course|no doubt)\.?$',
        r'^i (agree|know|see|got it|understand)\.?$',
    ]

    # Words that suggest possible filler (for LLM adjudication)
    # Use word boundaries in word boundary checking below
    FILLER_INDICATORS = ['yeah', 'yup', 'yep', 'okay', 'ok', 'right', 'sure', 'alright', 'correct', 'true', 'exactly', 'definitely', 'absolutely', 'got it', 'i see', 'mm-hmm', 'uh-huh']

    def is_obvious_filler(text: str) -> bool:
        """Check if text is an obvious filler (regex match)."""
        normalized = text.lower().strip()
        if len(normalized) > 25:
            return False
        for pattern in OBVIOUS_FILLER_PATTERNS:
            if re.match(pattern, normalized, re.IGNORECASE):
                return True
        return False

    def is_borderline(text: str) -> bool:
        """Check if text is a borderline case needing LLM adjudication."""
        normalized = text.lower().strip()
        # Borderline: 15-80 chars, starts with or contains filler words, but has more content
        if len(normalized) < 15 or len(normalized) > 80:
            return False
        # Use word boundaries to avoid false positives (e.g., "sure" in "pressure")
        return any(re.search(rf'\b{re.escape(ind)}\b', normalized) for ind in FILLER_INDICATORS)

    def llm_classify_filler(texts: list[str]) -> list[bool]:
        """Use local LLM to classify borderline statements. Returns list of is_filler bools."""
        if not texts:
            return []

        try:
            client = OpenAI(base_url=LM_STUDIO_URL, api_key="not-needed")
        except Exception as e:
            print(f"Warning: Could not connect to LM Studio: {e}. Keeping all borderline items.")
            return [False] * len(texts)  # Fallback: keep all borderline items

        # Batch process all statements in one call for efficiency
        # Format: "1. statement\n2. statement\n..."
        numbered_statements = "\n".join([f'{i+1}. "{text}"' for i, text in enumerate(texts)])

        prompt = f"""Classify each statement from a business call transcript.
Is it FILLER (just agreement/acknowledgment with no real information) or CONTENT (has meaningful information)?

Statements:
{numbered_statements}

Reply with only the line number and classification, one per line:
1. FILLER
2. CONTENT
etc."""

        try:
            response = client.chat.completions.create(
                model="qwen2.5-coder-1.5b-instruct-mlx",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=len(texts) * 10,
                temperature=0
            )
            answer = response.choices[0].message.content.strip().upper()

            # Parse responses (format: "1. FILLER", "2. CONTENT", etc.)
            results = []
            lines = answer.split('\n')
            for i, line in enumerate(lines):
                if i < len(texts):
                    if "FILLER" in line:
                        results.append(True)
                    elif "CONTENT" in line:
                        results.append(False)
                    else:
                        # Unexpected format - default to keep (False = not filler)
                        results.append(False)

            # If parsing returned fewer results than expected, pad with False (keep)
            while len(results) < len(texts):
                results.append(False)

            return results[:len(texts)]  # Return only as many results as texts
        except Exception as e:
            print(f"Warning: LLM classification failed: {e}. Keeping all borderline items.")
            return [False] * len(texts)  # Fallback: keep all borderline items

    lines = []
    participants = set()
    filtered_count = 0
    llm_filtered_count = 0
    borderline_items = []  # (index, speaker, text) for LLM adjudication

    # Try to detect format: CSV vs plain text
    all_rows = []

    # Check if it looks like CSV (has quotes and commas in first few lines)
    first_lines = raw_text.strip().split('\n')[:5]
    is_csv = any('"' in line and ',' in line for line in first_lines)

    if is_csv:
        # Parse CSV - Dialpad format: "timestamp","speaker","text"
        reader = csv.reader(StringIO(raw_text))
        for row in reader:
            if len(row) >= 3:
                speaker = row[1].strip()
                text = row[2].strip()
                if speaker and text and speaker.lower() != 'name' and text.lower() != 'content':
                    participants.add(speaker)
                    all_rows.append({"speaker": speaker, "text": text})
    else:
        # Parse plain text format: "Name   Timestamp[Content]"
        # Pattern: Name (multi-word), whitespace, timestamp (MM:SS or H:MM:SS), content (no separator)
        pattern = r'^([A-Za-z\s]+?)\s+(\d{1,2}:\d{2})(.*)$'
        for line in raw_text.strip().split('\n'):
            line = line.strip()
            if not line:
                continue
            match = re.match(pattern, line)
            if match:
                speaker = match.group(1).strip()
                # timestamp = match.group(2)  # Not used, but available if needed
                text = match.group(3).strip()
                if speaker and text:
                    participants.add(speaker)
                    all_rows.append({"speaker": speaker, "text": text})

    # First pass: obvious fillers and identify borderline
    for i, row in enumerate(all_rows):
        text = row["text"]
        if filter_fillers and is_obvious_filler(text):
            filtered_count += 1
            row["_status"] = "filtered"
        elif filter_fillers and llm_adjudicate and is_borderline(text):
            row["_status"] = "borderline"
            borderline_items.append((i, text))
        else:
            row["_status"] = "keep"

    # Second pass: LLM adjudication for borderline cases
    if borderline_items and llm_adjudicate:
        borderline_texts = [item[1] for item in borderline_items]
        llm_results = llm_classify_filler(borderline_texts)
        for (i, _), is_filler in zip(borderline_items, llm_results):
            if is_filler:
                all_rows[i]["_status"] = "filtered"
                llm_filtered_count += 1
            else:
                all_rows[i]["_status"] = "keep"

    # Collect kept rows
    for row in all_rows:
        if row.get("_status") == "keep":
            lines.append({"speaker": row["speaker"], "text": row["text"]})

    if not lines:
        return {"text": raw_text, "participants": [], "turn_count": 0, "filtered_count": filtered_count, "llm_filtered_count": llm_filtered_count}

    # Merge consecutive turns by same speaker
    if merge_speaker_turns:
        merged = []
        current_speaker = None
        current_texts = []

        for line in lines:
            if line["speaker"] == current_speaker:
                current_texts.append(line["text"])
            else:
                if current_speaker and current_texts:
                    merged.append({
                        "speaker": current_speaker,
                        "text": " ".join(current_texts)
                    })
                current_speaker = line["speaker"]
                current_texts = [line["text"]]

        # Don't forget last turn
        if current_speaker and current_texts:
            merged.append({
                "speaker": current_speaker,
                "text": " ".join(current_texts)
            })

        lines = merged

    # Format as clean text with speaker attribution
    formatted_lines = []
    for line in lines:
        formatted_lines.append(f"[{line['speaker']}] {line['text']}")

    return {
        "text": "\n\n".join(formatted_lines),
        "participants": sorted(list(participants)),
        "turn_count": len(lines),
        "filtered_count": filtered_count,
        "llm_filtered_count": llm_filtered_count
    }
