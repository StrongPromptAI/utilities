"""KB ingest target — writes the KB Postgres `reference_docs` /
`reference_doc_chunks` corpus, with figures uploaded to a CONFIGURABLE
destination and referenced as inline markdown links in chunk text (KB's chunk
table is text-only, so no schema migration — the link rides inside `text`).

Figure destination is NOT hardcoded. The ingest orchestration supplies an
`uploader`; the adapter fails closed if a doc has figures and none is given (it
will not silently pick a store). oxp.files is deliberately NOT a default — that
surface is reserved for OrthoXpress client work. The real KB destination is
`HttpUploadUploader` → the coach service's volume (kb project, internal routing;
see plan 26-6-20). `LocalDirUploader` (any folder) and `S3PublicUploader` (any
S3-compatible bucket) are generic fallbacks.

Embedding: in-process ONNX (`force_cloud=False`) — the SAME path KB search
queries use, so stored and query vectors match by construction.

Dependencies (uploader, db_factory) are injected so the adapter is
offline-verifiable (PY-1b) with fakes.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable, Optional

from ..embed_batch import embed_batch_texts
from ..ir import Chunk
from .base import DocMeta, WriteResult, figure_object_name


# Uploader = callable(local_path, object_name) -> public_url.
Uploader = Callable[[str, str], str]

_CTYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}


class LocalDirUploader:
    """Simplest 'anywhere folder' destination: copy the figure into `dest_dir`
    and return `<public_base_url>/<object_name>`. `dest_dir` is whatever the
    orchestration chose (a synced folder, a static-served dir, etc.) — this
    adapter does not care where, only that the URL resolves for readers."""

    def __init__(self, dest_dir: str, public_base_url: str):
        self.dest = Path(dest_dir)
        self.dest.mkdir(parents=True, exist_ok=True)
        self.public_base_url = public_base_url.rstrip("/")

    def __call__(self, local_path: str, object_name: str) -> str:
        shutil.copy2(local_path, self.dest / object_name)
        return f"{self.public_base_url}/{object_name}"


class HttpUploadUploader:
    """The real KB destination: POST the figure to the coach service's token-auth
    upload endpoint, which writes it to the coach Railway volume and serves it at
    `<base_url>/figures/<name>`. Ingestion is headless on a laptop and can't write
    a Railway volume directly, so it uploads over HTTP. See the coach plan
    (26-6-20) Phase 0 for the service contract."""

    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self._token = token

    def __call__(self, local_path: str, object_name: str) -> str:
        import httpx
        ctype = _CTYPES.get(Path(object_name).suffix.lower(), "application/octet-stream")
        with open(local_path, "rb") as fh:
            resp = httpx.post(
                f"{self.base_url}/figures",
                headers={"Authorization": f"Bearer {self._token}"},
                files={"file": (object_name, fh, ctype)},
                timeout=60.0,
            )
        resp.raise_for_status()
        return f"{self.base_url}/figures/{object_name}"


class S3PublicUploader:
    """Generic S3-compatible destination for any bucket the orchestration names.
    NOT wired to oxp.files by default — pass whatever endpoint/bucket/prefix/creds
    the chosen figure store uses."""

    def __init__(self, *, endpoint: str, bucket: str, access_key: str, secret_key: str,
                 public_base_url: str, prefix: str = "", region: str = "auto"):
        import boto3
        self._s3 = boto3.client(
            "s3", endpoint_url=endpoint, aws_access_key_id=access_key,
            aws_secret_access_key=secret_key, region_name=region,
        )
        self.bucket = bucket
        self.prefix = prefix
        self.public_base_url = public_base_url.rstrip("/")

    def __call__(self, local_path: str, object_name: str) -> str:
        ctype = _CTYPES.get(Path(object_name).suffix.lower(), "application/octet-stream")
        with open(local_path, "rb") as fh:
            self._s3.put_object(Bucket=self.bucket, Key=f"{self.prefix}{object_name}",
                                Body=fh, ContentType=ctype)
        return f"{self.public_base_url}/{object_name}"


class KBTarget:
    """IngestTarget for the KB reference-docs corpus."""

    def __init__(
        self,
        uploader: Optional[Uploader] = None,
        db_factory: Optional[Callable] = None,
    ):
        # uploader is REQUIRED when the doc has figures — the orchestration asks
        # where figures go and supplies it. No default store (never oxp.files).
        self._uploader = uploader
        self._db_factory = db_factory

    # — IngestTarget —

    def chunk_config(self) -> tuple[int, int]:
        return (100, 3000)  # book prose

    def enrich(self, chunk: Chunk) -> None:
        return  # KB stores no classification — no-op (the seam's clean half)

    def stage_image(self, chunk: Chunk, doc_slug: str, index: int) -> None:
        if not chunk.img_local_path:
            return
        name = figure_object_name(doc_slug, index, chunk.img_local_path)
        url = self._upload(chunk.img_local_path, name)
        chunk.img_ref = url
        # KB has no image column → embed the figure as an inline markdown link so
        # it survives chunking, embeds with its caption, and renders in any md surface.
        cap = chunk.caption or "figure"
        chunk.text = f"{chunk.text}\n\n![{cap}]({url})"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return embed_batch_texts(texts, force_cloud=False)

    def write(self, doc: DocMeta, chunks: list[Chunk], embeddings: list[list[float]]) -> WriteResult:
        with self._db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM reference_docs WHERE title=%s AND category=%s",
                    (doc.title, doc.category),
                )
                row = cur.fetchone()
                if row:
                    doc_id = row["id"]
                    cur.execute(
                        "UPDATE reference_docs SET content=%s, source_file=%s WHERE id=%s",
                        (doc.markdown, doc.source_file, doc_id),
                    )
                else:
                    cur.execute(
                        "INSERT INTO reference_docs (title, category, content, source_file) "
                        "VALUES (%s,%s,%s,%s) RETURNING id",
                        (doc.title, doc.category, doc.markdown, doc.source_file),
                    )
                    doc_id = cur.fetchone()["id"]
            with conn.cursor() as cur:
                cur.execute("DELETE FROM reference_doc_chunks WHERE doc_id=%s", (doc_id,))
                for idx, (c, emb) in enumerate(zip(chunks, embeddings)):
                    cur.execute(
                        "INSERT INTO reference_doc_chunks (doc_id, chunk_idx, text, embedding) "
                        "VALUES (%s,%s,%s,%s)",
                        (doc_id, idx, c.text, emb),
                    )
            conn.commit()
        image_count = sum(1 for c in chunks if c.chunk_type == "image" and c.img_ref)
        return WriteResult(doc_id=doc_id, chunk_count=len(chunks), image_count=image_count)

    # — lazy real deps —

    def _upload(self, local_path: str, object_name: str) -> str:
        if self._uploader is None:
            raise RuntimeError(
                "KBTarget has figures to stage but no uploader configured. The ingest "
                "orchestration must ask where figures go and pass uploader= "
                "(LocalDirUploader or S3PublicUploader). There is no default store."
            )
        return self._uploader(local_path, object_name)

    def _db(self):
        if self._db_factory is None:
            import sys
            sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # scripts/
            from kb_core import get_db
            self._db_factory = get_db
        return self._db_factory()
