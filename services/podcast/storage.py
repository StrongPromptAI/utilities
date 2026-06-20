"""Volume helpers — the audio lives on the Railway volume, not in a bucket.

Layout (under PODCAST_AUDIO_ROOT, default `/data/audio`):
    <folder>/<name>.mp3     episode audio
    <folder>/<name>.md      optional transcript sidecar → default description
    <folder>/_art.<ext>     optional cover art → <itunes:image>

Everything here resolves paths *inside* the audio root and refuses traversal —
filenames are basename-only, folders are single-segment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

AUDIO_ROOT = Path(os.environ.get("PODCAST_AUDIO_ROOT", "/data/audio")).expanduser()

_ART_EXTS = (".png", ".jpg", ".jpeg", ".webp")
_DESCRIPTION_CAP = 2000  # transcript sidecars can be long; cap the default


@dataclass
class AudioFile:
    name: str          # basename, e.g. HealingJourneyPodcast_EP1.mp3
    size: int          # bytes — feeds the <enclosure length=...>
    mtime: float       # epoch seconds — default <pubDate>
    sidecar: str | None  # transcript text (capped) if a .md sits beside it


def _safe_folder(folder: str) -> str:
    if not folder or "/" in folder or "\\" in folder or folder.startswith("."):
        raise ValueError("invalid folder")
    return folder


def _safe_name(name: str) -> str:
    if not name or "/" in name or "\\" in name or ".." in name or name.startswith("."):
        raise ValueError("invalid name")
    return name


def folder_dir(folder: str) -> Path:
    return AUDIO_ROOT / _safe_folder(folder)


def list_audio(folder: str) -> list[AudioFile]:
    """Every *.mp3 in a show's folder, with its size, mtime, and sidecar text."""
    d = folder_dir(folder)
    if not d.is_dir():
        return []
    out: list[AudioFile] = []
    for p in d.iterdir():
        if p.suffix.lower() != ".mp3" or not p.is_file() or p.name.startswith("."):
            continue
        st = p.stat()
        sidecar_path = p.with_suffix(".md")
        sidecar = None
        if sidecar_path.is_file():
            sidecar = sidecar_path.read_text(encoding="utf-8", errors="replace").strip()
            if len(sidecar) > _DESCRIPTION_CAP:
                sidecar = sidecar[:_DESCRIPTION_CAP].rstrip() + "…"
        out.append(AudioFile(name=p.name, size=st.st_size, mtime=st.st_mtime, sidecar=sidecar))
    return out


def audio_path(folder: str, name: str) -> Path | None:
    """Resolved path to one MP3, or None if absent. Refuses traversal."""
    name = _safe_name(name)
    if not name.lower().endswith(".mp3"):
        return None
    p = folder_dir(folder) / name
    # Defense in depth: the resolved path must stay under the folder dir.
    try:
        p.resolve().relative_to(folder_dir(folder).resolve())
    except ValueError:
        return None
    return p if p.is_file() else None


def artwork_path(folder: str) -> Path | None:
    """First `_art.<ext>` in the folder, or None."""
    d = folder_dir(folder)
    for ext in _ART_EXTS:
        p = d / f"_art{ext}"
        if p.is_file():
            return p
    return None


def write_upload(folder: str, name: str, data: bytes) -> Path:
    """Persist an uploaded MP3 (or sidecar) into the show folder."""
    name = _safe_name(name)
    d = folder_dir(folder)
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_bytes(data)
    return p


def delete_file(folder: str, name: str) -> bool:
    """Remove a file from a show folder (traversal-safe). Returns True if removed."""
    name = _safe_name(name)
    p = folder_dir(folder) / name
    try:
        p.resolve().relative_to(folder_dir(folder).resolve())
    except ValueError:
        return False
    if p.is_file():
        p.unlink()
        return True
    return False
