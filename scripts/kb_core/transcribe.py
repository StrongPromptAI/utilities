"""Audio transcription via MLX Whisper."""

import json
from pathlib import Path


def transcribe_audio(
    audio_path: str,
    model: str = "mlx-community/whisper-large-v3-turbo",
) -> dict:
    """Transcribe an audio file to KB JSON format using MLX Whisper.

    Accepts any format Whisper supports: MP3, WAV, M4A, FLAC.
    Writes a .kb.json file alongside the audio with extended format
    including timestamps.

    Args:
        audio_path: Path to audio file
        model: MLX Whisper model identifier

    Returns:
        {"json_path": str, "segments": int, "duration_min": float, "chars": int}
    """
    import mlx_whisper

    path = Path(audio_path)
    if not path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    result = mlx_whisper.transcribe(
        str(path),
        path_or_hf_repo=model,
    )

    # Convert Whisper segments to KB JSON format
    segments = []
    for seg in result.get("segments", []):
        segments.append({
            "speaker": "Speaker",
            "text": seg["text"].strip(),
            "start": round(seg["start"], 2),
            "end": round(seg["end"], 2),
        })

    # Write JSON alongside audio file
    json_path = path.with_suffix(".kb.json")
    json_path.write_text(json.dumps(segments, indent=2, ensure_ascii=False))

    # Compute stats
    total_chars = sum(len(s["text"]) for s in segments)
    duration_min = segments[-1]["end"] / 60.0 if segments else 0.0

    return {
        "json_path": str(json_path),
        "segments": len(segments),
        "duration_min": round(duration_min, 1),
        "chars": total_chars,
    }
