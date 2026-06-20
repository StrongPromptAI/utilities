# doc_ingest — backend-agnostic document ingestion

MinerU PDF/scan extraction → image-aware chunks → a pluggable `IngestTarget`
(KB `reference_docs`, or thj `resource_chunk` via the adapter in the thj repo).
Plan: `symlink_docs/plans/26-6-19-document-ingestion-utility.md`.

## ⚠️ A `git pull` is NOT enough — run the setup below

The Python code propagates via pull, but the **MinerU runtime does not**: the
extraction venv is gitignored and the VLM models (~1–2 GB) live in the HF cache.
Each developer runs this once.

### Two-venv architecture (why setup has two parts)

- **MinerU runs in its OWN 3.12 venv**, invoked as a subprocess via `MINERU_BIN`.
  MinerU pins Python 3.11–3.12 (doclayout-yolo) — it cannot share a 3.13 repo venv.
- **The doc_ingest code + adapters run under the consuming repo's venv**
  (utilities for KB ingest; thj's venv for the equipment adapter), which already
  has `kb_core` / `nomic_onnx_embed` / `psycopg`. The `extract.py` subprocess
  boundary is what lets a 3.13 caller drive a 3.12 MinerU.

### Setup — Apple Silicon (MLX, recommended)

```bash
cd ~/repos/utilities/scripts/doc_ingest
uv venv --python 3.12 .venv
VIRTUAL_ENV="$PWD/.venv" uv pip install "mineru[mlx]>=3.4.0" torch torchvision
#   torch+torchvision are REQUIRED even on MLX — the transformers Qwen2-VL
#   processor imports them; without them the run fails at processor load.
.venv/bin/mineru-models-download -s huggingface -m vlm     # ~1–2 GB, one time
export MINERU_BIN="$PWD/.venv/bin/mineru"                   # add to your shell profile
```

### Setup — Intel mac / Linux (CPU pipeline)

`vlm-engine`/MLX is Apple-Silicon only. Elsewhere use the CPU `pipeline` backend:

```bash
uv venv --python 3.12 .venv
VIRTUAL_ENV="$PWD/.venv" uv pip install "mineru[pipeline]>=3.4.0"
.venv/bin/mineru-models-download -s huggingface -m pipeline
export MINERU_BIN="$PWD/.venv/bin/mineru"
# then pass backend="pipeline" (extract(..., backend="pipeline"))
```

### Secrets (per machine, not in git)

`~/.config/keys.json` needs `railway_main` (KB Postgres) and, to upload book
figures to the coach service, `coach_upload_secret`. In-process embedding needs
`nomic_onnx_embed` in the *consuming* repo venv (already present in utilities).

## Run

```bash
# KB book ingest (utilities 3.13 venv runs the code; MinerU is the 3.12 subprocess)
MINERU_BIN=~/repos/utilities/scripts/doc_ingest/.venv/bin/mineru \
COACH_UPLOAD_SECRET=$(python3 -c "import json,os;print(json.load(open(os.path.expanduser('~/.config/keys.json')))['coach_upload_secret'])") \
uv run python scripts/ingest_book_pdf.py "<book>.pdf" <category> "<Title>" \
    --figures-url https://coach-production-e685.up.railway.app --batch-pages 60
# --batch-pages 60 = resumable batches (NEVER a monolithic run on a large book).
# Re-running skips already-extracted batches.

uv run python scripts/doc_ingest/smoke.py --method-dir <vlm_dir>   # core round-trip
uv run python scripts/doc_ingest/lint_core_no_equipment.py         # seam guard
```

## thj equipment adapter (other repo)

`thj/ingestion_mineru/src/equipment_manuals/targets/thj.py` imports this core via
`DOC_INGEST_PATH` (defaults to `~/repos/utilities/scripts`). If utilities is
checked out elsewhere, set `DOC_INGEST_PATH` accordingly. (Pinning this as a real
dependency instead of a sys.path bridge is the deferred Phase-3 item.)
