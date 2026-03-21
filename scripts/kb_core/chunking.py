"""Text chunking functions."""

import re
from .config import DEFAULT_CHUNK_SIZE, DEFAULT_OVERLAP, TRANSCRIPT_TARGET_CHUNK_SIZE


def chunk_text(text: str, chunk_size: int = DEFAULT_CHUNK_SIZE, overlap: int = DEFAULT_OVERLAP) -> list[str]:
    """Fixed-size chunking with overlap. Use for raw transcripts."""
    chunks = []
    start = 0
    while start < len(text):
        chunk = text[start:start + chunk_size]
        if chunk.strip():
            chunks.append(chunk)
        start += chunk_size - overlap
    return chunks


def chunk_by_sections(text: str, min_chunk_size: int = 50) -> list[str]:
    """Section-based chunking for structured notes.

    Splits on:
    - Markdown headers (## or ###)
    - Numbered sections (1., 2., etc. at line start)
    - Lettered subsections (a), b), etc. at line start)

    Use for structured notes where semantic units should be preserved.
    """
    lines = text.split('\n')
    chunks = []
    current_chunk_lines = []
    current_header = ""

    # Patterns that indicate a new section
    section_patterns = [
        r'^#{1,4}\s+',           # Markdown headers
        r'^\d+\.\s+[A-Z]',       # Numbered sections starting with caps (1. TITLE)
        r'^[a-z]\)\s+[A-Z]',     # Lettered subsections (a) TITLE)
        r'^[A-Z][A-Z\s]+:$',     # ALL CAPS HEADER:
        r'^[A-Z][A-Z\s]+$',      # ALL CAPS LINE (standalone header)
    ]

    def is_section_start(line: str) -> bool:
        stripped = line.strip()
        if not stripped:
            return False
        for pattern in section_patterns:
            if re.match(pattern, stripped):
                return True
        return False

    def flush_chunk():
        nonlocal current_chunk_lines, current_header
        if current_chunk_lines:
            chunk_text = '\n'.join(current_chunk_lines).strip()
            if len(chunk_text) >= min_chunk_size:
                # Prepend header context if we have one
                if current_header and not chunk_text.startswith(current_header):
                    chunk_text = f"{current_header}\n{chunk_text}"
                chunks.append(chunk_text)
            current_chunk_lines = []

    for line in lines:
        if is_section_start(line):
            flush_chunk()
            current_header = line.strip()
            current_chunk_lines = [line]
        else:
            current_chunk_lines.append(line)

    # Don't forget the last chunk
    flush_chunk()

    # If no sections found, fall back to paragraph chunking
    if not chunks:
        chunks = [p.strip() for p in text.split('\n\n') if p.strip() and len(p.strip()) >= min_chunk_size]

    # If still nothing, return the whole text as one chunk
    if not chunks and text.strip():
        chunks = [text.strip()]

    return chunks


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences, handling common abbreviations."""
    # Split on sentence-ending punctuation followed by space + uppercase or newline
    parts = re.split(r'(?<=[.!?])\s+(?=[A-Z\n])', text)
    # Also split on newlines within turns
    sentences = []
    for part in parts:
        for line in part.split("\n"):
            s = line.strip()
            if s:
                sentences.append(s)
    return sentences


def chunk_transcript(
    text: str,
    min_chunk_size: int = 500,
    max_chunk_size: int = 700,
    turns: list[dict] | None = None,
) -> list[dict]:
    """Semantic chunking of a preprocessed transcript.

    Splits on sentence boundaries within speaker turns, targeting 500-700 chars.
    Never cuts mid-sentence. Preserves speaker attribution per chunk.

    Args:
        text: Preprocessed transcript (output of preprocess_transcript)
        min_chunk_size: Minimum chunk size before accepting a break
        max_chunk_size: Maximum chunk size — flush at next sentence boundary
        turns: Optional structured turn data from preprocess_transcript with
               timestamps. Each dict has speaker, text, and optional start/end.

    Returns:
        List of dicts: [{"speaker": str|None, "text": str,
                         "start_time": float|None, "end_time": float|None}, ...]
    """
    # Build parsed turns from structured data or from text
    parsed_turns: list[dict] = []

    if turns:
        parsed_turns = turns
    else:
        # Split on double newlines (speaker turn boundaries)
        raw_turns = [t.strip() for t in text.split("\n\n") if t.strip()]

        if not raw_turns:
            if text.strip():
                return [{"speaker": None, "text": text.strip(), "start_time": None, "end_time": None}]
            return []

        def parse_turn(turn: str) -> dict:
            match = re.match(r'^\[([^\]]+)\]\s*(.*)$', turn, re.DOTALL)
            if match:
                return {"speaker": match.group(1), "text": match.group(2).strip()}
            return {"speaker": None, "text": turn}

        parsed_turns = [parse_turn(t) for t in raw_turns]

    if not parsed_turns:
        return []

    # Build a flat list of (speaker, sentence, start, end) tuples
    # All sentences from a turn share that turn's start/end
    segments: list[tuple[str | None, str, float | None, float | None]] = []
    for turn in parsed_turns:
        start = turn.get("start")
        end = turn.get("end")
        for sentence in _split_sentences(turn["text"]):
            segments.append((turn.get("speaker"), sentence, start, end))

    if not segments:
        return []

    # Group sentences into chunks respecting size bounds
    chunks = []
    current_sentences: list[str] = []
    current_speakers: list[str] = []
    current_starts: list[float] = []
    current_ends: list[float] = []
    current_size = 0

    def _flush():
        chunk_text = " ".join(current_sentences)
        primary = max(set(current_speakers), key=current_speakers.count) if current_speakers else None
        chunk = {
            "speaker": primary,
            "text": chunk_text,
            "start_time": min(current_starts) if current_starts else None,
            "end_time": max(current_ends) if current_ends else None,
        }
        chunks.append(chunk)

    for speaker, sentence, start, end in segments:
        sentence_size = len(sentence)

        # If adding this sentence would exceed max and we have enough content, flush
        if current_size + sentence_size > max_chunk_size and current_size >= min_chunk_size:
            _flush()
            current_sentences = []
            current_speakers = []
            current_starts = []
            current_ends = []
            current_size = 0

        current_sentences.append(sentence)
        if speaker:
            current_speakers.append(speaker)
        if start is not None:
            current_starts.append(start)
        if end is not None:
            current_ends.append(end)
        current_size += sentence_size + 1  # +1 for space join

    # Flush remaining
    if current_sentences:
        _flush()

    return chunks
