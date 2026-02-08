"""Transcript preprocessing functions."""

import json
import re
import csv
from io import StringIO
from pathlib import Path
from openai import OpenAI
from .config import LM_STUDIO_URL, SUMMARY_MODEL

MAX_LLM_BATCH = 15  # Stay within qwen2.5-coder-1.5b context window


def _extract_docx(file_path: str) -> str:
    """Extract text from Teams DOCX transcript.

    Teams DOCX structure: each paragraph has optional speaker icon (image)
    followed by text in format: \nName   Timestamp\nContent

    Images are automatically excluded by python-docx .text property.
    """
    from docx import Document
    doc = Document(file_path)
    return '\n'.join(p.text for p in doc.paragraphs if p.text.strip())


def _detect_text_format(text: str) -> str:
    """Detect transcript text format from content.

    Returns: 'csv', 'plaintext', or raises ValueError.
    """
    first_lines = text.strip().split('\n')[:5]

    # CSV: quotes + commas (Dialpad format)
    if any('"' in line and ',' in line for line in first_lines):
        return 'csv'

    # Plaintext: Name  Timestamp pattern (Teams text export, pipe-delimited, other)
    pattern = r'^[A-Za-z\s]+?\s*\|?\s*\d{1,2}:\d{2}'
    if any(re.match(pattern, line.strip()) for line in first_lines if line.strip()):
        return 'plaintext'

    # Unknown
    sample = text.strip()[:200]
    raise ValueError(f"Unknown transcript format. First 200 chars:\n{sample}")


def detect_and_extract(file_path: str) -> tuple[str, str]:
    """Detect format and extract text. Returns (text, format_name)."""
    path = Path(file_path)

    # Level 1: Binary/file-level formats
    if path.suffix.lower() == '.docx':
        text = _extract_docx(str(path))
        # After DOCX extraction, detect text-level format
        # Teams DOCX extracts to plaintext format
        try:
            fmt = _detect_text_format(text)
        except ValueError:
            fmt = 'plaintext'
        return text, fmt

    if path.suffix.lower() == '.json':
        raw = path.read_text(errors='replace')
        return raw, 'json'

    # Level 2: Text content detection
    raw = path.read_text(errors='replace')
    return raw, _detect_text_format(raw)


def preprocess_transcript(file_path: str, merge_speaker_turns: bool = True, filter_fillers: bool = True, llm_adjudicate: bool = True) -> dict:
    """Preprocess a transcript file (any supported format).

    Detects format (DOCX, CSV, plaintext), extracts text, strips timestamps,
    preserves speaker attribution, optionally merges consecutive turns by the
    same speaker, filters out agreement fillers.

    Args:
        file_path: Path to transcript file (DOCX, CSV, or plaintext)
        merge_speaker_turns: If True, merge consecutive lines from same speaker
        filter_fillers: If True, remove low-value agreement statements
        llm_adjudicate: If True, use local LLM to classify borderline cases

    Returns:
        {
            "text": cleaned transcript text,
            "participants": list of unique speakers,
            "turn_count": number of speaker turns,
            "filtered_count": number of filler turns removed,
            "llm_filtered_count": number filtered by LLM adjudication,
            "format": detected format name
        }
    """
    raw_text, fmt = detect_and_extract(file_path)

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
        if len(normalized) < 15 or len(normalized) > 80:
            return False
        return any(re.search(rf'\b{re.escape(ind)}\b', normalized) for ind in FILLER_INDICATORS)

    def _classify_batch(client: OpenAI, texts: list[str]) -> list[bool]:
        """Classify a single batch of texts. Returns list of is_filler bools."""
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
                model=SUMMARY_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=len(texts) * 10,
                temperature=0
            )
            answer = response.choices[0].message.content.strip().upper()

            results = []
            lines = answer.split('\n')
            for i, line in enumerate(lines):
                if i < len(texts):
                    if "FILLER" in line:
                        results.append(True)
                    elif "CONTENT" in line:
                        results.append(False)
                    else:
                        results.append(False)

            while len(results) < len(texts):
                results.append(False)

            return results[:len(texts)]
        except Exception as e:
            print(f"Warning: LLM classification failed: {e}. Keeping all borderline items.")
            return [False] * len(texts)

    def llm_classify_filler(texts: list[str]) -> list[bool]:
        """Use local LLM to classify borderline statements in batches. Returns list of is_filler bools."""
        if not texts:
            return []

        try:
            client = OpenAI(base_url=LM_STUDIO_URL, api_key="not-needed")
        except Exception as e:
            print(f"Warning: Could not connect to LM Studio: {e}. Keeping all borderline items.")
            return [False] * len(texts)

        results = []
        for batch_start in range(0, len(texts), MAX_LLM_BATCH):
            batch = texts[batch_start:batch_start + MAX_LLM_BATCH]
            batch_results = _classify_batch(client, batch)
            results.extend(batch_results)
        return results

    lines = []
    participants = set()
    filtered_count = 0
    llm_filtered_count = 0
    borderline_items = []
    all_rows = []

    if fmt == 'json':
        # JSON array of {speaker, text} objects (no timestamps)
        entries = json.loads(raw_text)
        for entry in entries:
            speaker = entry.get("speaker", "Unknown").strip()
            text = entry.get("text", "").strip()
            if speaker and text:
                participants.add(speaker)
                all_rows.append({"speaker": speaker, "text": text})
    elif fmt == 'csv':
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
        # Parse plain text format. Two variants:
        # Single-line: "Name   0:03 Content on same line"
        # Multi-line (Teams DOCX): "Name   0:03\nContent on next line(s)"
        speaker_pattern = r'^([A-Za-z\s]+?)\s*\|\s*(\d{1,2}:\d{2})(.*)$|^([A-Za-z\s]+?)\s+(\d{1,2}:\d{2})(.*)$'
        current_speaker = None
        current_text_lines = []

        for line in raw_text.strip().split('\n'):
            stripped = line.strip()
            if not stripped:
                # Blank line: flush current turn if we have content
                if current_speaker and current_text_lines:
                    text = ' '.join(current_text_lines)
                    participants.add(current_speaker)
                    all_rows.append({"speaker": current_speaker, "text": text})
                    current_speaker = None
                    current_text_lines = []
                continue

            match = re.match(speaker_pattern, stripped)
            if match:
                # Flush previous turn
                if current_speaker and current_text_lines:
                    text = ' '.join(current_text_lines)
                    participants.add(current_speaker)
                    all_rows.append({"speaker": current_speaker, "text": text})

                current_speaker = match.group(1).strip()
                remainder = match.group(3).strip()
                current_text_lines = [remainder] if remainder else []
            elif current_speaker:
                # Content line belonging to current speaker
                current_text_lines.append(stripped)

        # Flush last turn
        if current_speaker and current_text_lines:
            text = ' '.join(current_text_lines)
            participants.add(current_speaker)
            all_rows.append({"speaker": current_speaker, "text": text})

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
        return {"text": raw_text, "participants": [], "turn_count": 0, "filtered_count": filtered_count, "llm_filtered_count": llm_filtered_count, "format": fmt}

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
        "llm_filtered_count": llm_filtered_count,
        "format": fmt
    }
