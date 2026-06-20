"""MinerU extraction wrapper → `Extraction` IR.

Runs the MinerU 3.4 CLI as a subprocess and parses its `content_list.json`.
Decoupled in two halves so parsing is testable without a multi-minute re-run:
  - `run_mineru(...)`  → runs the CLI, returns the method dir (fail-loud)
  - `parse_extraction(method_dir)` → reads the JSON/markdown into `Extraction`
  - `extract(...)`     → run + parse

MinerU-3.4 realities this guards against (all learned by running, 2026-06-19):
  - Backend flag is `vlm-engine` (MLX auto-selected on Apple Silicon), NOT
    `vlm-mlx-engine`. Output lands under `<out>/<name>/vlm/`, not `/auto/`.
  - The CLI **exits 0 even when the task fails internally** — so we never trust
    the return code; we assert `_content_list.json` exists and is non-empty.
  - `mineru[mlx]` needs `torch`+`torchvision` (the transformers Qwen2-VL
    processor); absent, the run fails at processor load (caught by the guard).
  - MinerU lives in its own 3.12 venv. Resolve its binary via `MINERU_BIN`
    (preferred) or PATH — the parsing code itself runs under any Python, so a
    caller on 3.13 drives a 3.12 MinerU through this subprocess boundary.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from .ir import Block, Extraction


class ExtractionError(RuntimeError):
    """MinerU failed, or produced no usable output. Raised loudly — never
    swallowed — so a broken extraction stops the pipeline at the source."""


def _resolve_mineru_bin() -> str:
    """Locate the MinerU CLI. MINERU_BIN wins; else PATH. We do not guess into a
    sibling venv — a missing binary is a loud, actionable error, not a fallback."""
    explicit = os.getenv("MINERU_BIN")
    if explicit:
        if not Path(explicit).is_file():
            raise ExtractionError(f"MINERU_BIN={explicit} is not a file.")
        return explicit
    found = shutil.which("mineru")
    if not found:
        raise ExtractionError(
            "mineru not found. Set MINERU_BIN to the CLI in the doc_ingest venv "
            "(scripts/doc_ingest/.venv/bin/mineru) or put it on PATH."
        )
    return found


def _find_method_dir(out_dir: Path, pdf_stem: str = "") -> Path:
    """MinerU writes `<out>/<name>/<method>/` where <method> is backend-named
    (`vlm` for vlm-engine, `auto` for pipeline). Discover the method dir by the
    `_content_list.json` it must contain — NOT by assuming `<name> == pdf_stem`:
    MinerU TRUNCATES long PDF names for the output subdir, so the stem-based
    lookup misses (caught 2026-06-20 on the 388pp Adizes book — a name long
    enough to be truncated mid-hash). `pdf_stem` is accepted for back-compat but
    not required."""
    if not out_dir.is_dir():
        raise ExtractionError(f"MinerU produced no output under {out_dir}")
    hits = sorted(out_dir.glob("*/*/*_content_list.json"))   # <name>/<method>/…_content_list.json
    if not hits:
        hits = sorted(out_dir.glob("**/*_content_list.json"))  # tolerate layout shifts
    if not hits:
        raise ExtractionError(
            f"No *_content_list.json under {out_dir} — extraction failed silently "
            "(MinerU exits 0 even on internal failure)."
        )
    return hits[0].parent


def run_mineru(
    pdf_path: str,
    out_dir: str,
    backend: str = "vlm-engine",
    start: Optional[int] = None,
    end: Optional[int] = None,
    lang: str = "en",
    timeout: int = 7200,
) -> Path:
    """Run MinerU on `pdf_path` into `out_dir`. Returns the method dir.

    `start`/`end` are 0-indexed inclusive page bounds (MinerU `-s`/`-e`) — use
    them to batch a large book; the full 388-page Adizes run must be chunked.
    Fail-loud: a non-zero exit OR a missing content_list both raise.
    """
    pdf = Path(pdf_path).resolve()
    if not pdf.is_file():
        raise ExtractionError(f"PDF not found: {pdf}")
    out = Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)

    cmd = [_resolve_mineru_bin(), "-p", str(pdf), "-o", str(out), "-b", backend, "-l", lang]
    if start is not None:
        cmd += ["-s", str(start)]
    if end is not None:
        cmd += ["-e", str(end)]

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    # The CLI is unreliable about exit codes (exits 0 on internal task failure),
    # so the return code is advisory only — the real gate is _find_method_dir.
    method_dir = _find_method_dir(out, pdf.stem)
    if proc.returncode != 0:
        # Output exists but the CLI also reported failure — surface both.
        tail = (proc.stderr or proc.stdout or "")[-800:]
        raise ExtractionError(
            f"mineru exited {proc.returncode} (output dir {method_dir} exists but "
            f"may be partial). stderr tail:\n{tail}"
        )
    return method_dir


def parse_extraction(method_dir: str | Path, source_pdf: str = "") -> Extraction:
    """Parse a MinerU method dir into the `Extraction` IR. Pure I/O + JSON — runs
    under any Python version, independent of the MinerU venv."""
    md = Path(method_dir)
    cl = next(md.glob("*_content_list.json"), None)
    if cl is None:
        raise ExtractionError(f"No *_content_list.json in {md}")
    raw = json.loads(cl.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise ExtractionError(f"{cl.name} is empty or not a list — extraction yielded nothing.")

    blocks = [Block.model_validate(b) for b in raw]
    md_file = next(md.glob("*.md"), None)
    markdown = md_file.read_text(encoding="utf-8") if md_file else ""
    page_count = max((b.page_idx for b in blocks), default=-1) + 1

    return Extraction(
        blocks=blocks,
        markdown=markdown,
        page_count=page_count,
        method_dir=str(md),
        images_dir=str(md / "images"),
        source_pdf=source_pdf or str(cl),
    )


def extract(
    pdf_path: str,
    out_dir: str,
    backend: str = "vlm-engine",
    start: Optional[int] = None,
    end: Optional[int] = None,
) -> Extraction:
    """Run MinerU then parse — the one-call path for callers that want both."""
    method_dir = run_mineru(pdf_path, out_dir, backend=backend, start=start, end=end)
    return parse_extraction(method_dir, source_pdf=pdf_path)


def pdf_page_count(pdf_path: str) -> int:
    """Total pages via `pdfinfo` (poppler). Raises if unavailable — the batched
    path needs an authoritative count, not a guess."""
    import re
    if not shutil.which("pdfinfo"):
        raise ExtractionError("pdfinfo (poppler) not found — needed to compute batch ranges. brew install poppler.")
    out = subprocess.run(["pdfinfo", pdf_path], capture_output=True, text=True)
    m = re.search(r"^Pages:\s+(\d+)", out.stdout, re.MULTILINE)
    if not m:
        raise ExtractionError(f"could not read page count from pdfinfo:\n{out.stdout[:300]}")
    return int(m.group(1))


def extract_batched(
    pdf_path: str,
    out_dir: str,
    batch_pages: int = 60,
    total_pages: Optional[int] = None,
    backend: str = "vlm-engine",
    log=print,
) -> Extraction:
    """Extract a large PDF in resumable page-range batches, then assemble into one
    `Extraction`. Each batch writes its own durable dir under `out_dir/batch_<start>`;
    a re-run SKIPS any batch already holding a content_list (so a failure only
    re-runs the unfinished tail). Avoids the monolithic run's all-or-nothing risk.

    Assembly: page_idx is offset to absolute; every batch's cropped figures are
    copied into one combined images dir (MinerU's content-hash filenames don't
    collide), so chunk.py's basename-join resolves them. Blocks concatenate in
    page order (batches are sequential ranges) → document reading order preserved.
    """
    pdf = Path(pdf_path).resolve()
    total = total_pages or pdf_page_count(str(pdf))
    out = Path(out_dir).resolve()
    combined_images = out / "images_combined"
    combined_images.mkdir(parents=True, exist_ok=True)

    blocks: list[Block] = []
    markdowns: list[str] = []
    n_batches = (total + batch_pages - 1) // batch_pages
    for bi, start in enumerate(range(0, total, batch_pages)):
        end = min(start + batch_pages - 1, total - 1)
        bdir_root = out / f"batch_{start:04d}"
        # Resume: reuse an already-extracted batch.
        try:
            method_dir = _find_method_dir(bdir_root, pdf.stem)
            log(f"[batch {bi + 1}/{n_batches}] pp{start}-{end}: cached, skipping extract")
        except ExtractionError:
            log(f"[batch {bi + 1}/{n_batches}] pp{start}-{end}: extracting…")
            method_dir = run_mineru(str(pdf), str(bdir_root), backend=backend, start=start, end=end)
        ext = parse_extraction(method_dir, source_pdf=str(pdf))
        for b in ext.blocks:
            b.page_idx += start                       # batch-relative → absolute
        blocks.extend(ext.blocks)
        markdowns.append(ext.markdown)
        # Pool the batch's figures into the combined dir (hash names → no collision).
        src_imgs = Path(ext.images_dir)
        if src_imgs.is_dir():
            for img in src_imgs.iterdir():
                if img.is_file():
                    shutil.copy2(img, combined_images / img.name)
        log(f"[batch {bi + 1}/{n_batches}] pp{start}-{end}: {len(ext.blocks)} blocks, "
            f"{sum(1 for b in ext.blocks if b.type == 'image')} figures")

    log(f"assembled {len(blocks)} blocks across {n_batches} batches")
    return Extraction(
        blocks=blocks,
        markdown="\n\n".join(markdowns),
        page_count=total,
        method_dir=str(out),
        images_dir=str(combined_images),
        source_pdf=str(pdf),
    )
