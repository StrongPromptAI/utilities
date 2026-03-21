"""Document storage backend — S3-compatible (Railway storage bucket).

Uses boto3 to read/write markdown docs from a Railway-provisioned bucket.
Falls back to stub content when S3 credentials are not configured (local dev).
"""
import os
import logging

log = logging.getLogger(__name__)

# S3 config — set by Railway bucket wiring
_S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "")
_S3_ACCESS_KEY = os.environ.get("S3_ACCESS_KEY", "")
_S3_SECRET_KEY = os.environ.get("S3_SECRET_KEY", "")
_S3_BUCKET = os.environ.get("S3_BUCKET", "")
_S3_REGION = os.environ.get("S3_REGION", "auto")

# Prefix for docs in the bucket
_PREFIX = "docs/"

# Known stakeholder doc paths (for listing)
STAKEHOLDER_DOCS = [
    "stakeholders/itheraputix/doctor.md",
    "stakeholders/itheraputix/patient.md",
    "stakeholders/itheraputix/physical-therapist.md",
    "stakeholders/itheraputix/dme-provider.md",
    "stakeholders/itheraputix/dme-provider-sales.md",
    "stakeholders/itheraputix/investor.md",
]


def _get_client():
    """Get boto3 S3 client. Returns None if not configured."""
    if not (_S3_ENDPOINT and _S3_ACCESS_KEY and _S3_SECRET_KEY and _S3_BUCKET):
        return None
    import boto3
    return boto3.client(
        "s3",
        endpoint_url=_S3_ENDPOINT,
        aws_access_key_id=_S3_ACCESS_KEY,
        aws_secret_access_key=_S3_SECRET_KEY,
        region_name=_S3_REGION,
    )


def list_docs() -> list[dict]:
    """List available document paths from bucket or fallback to known list."""
    client = _get_client()
    if client:
        try:
            resp = client.list_objects_v2(Bucket=_S3_BUCKET, Prefix=_PREFIX)
            paths = []
            for obj in resp.get("Contents", []):
                key = obj["Key"]
                if key.endswith(".md"):
                    path = key[len(_PREFIX):]  # strip prefix
                    paths.append({"path": path, "label": _label(path)})
            if paths:
                return paths
        except Exception as e:
            log.warning("S3 list failed: %s", e)
    # Fallback to known list
    return [{"path": p, "label": _label(p)} for p in STAKEHOLDER_DOCS]


def read_doc(path: str) -> str | None:
    """Read markdown content from S3 bucket."""
    client = _get_client()
    if client:
        try:
            key = _PREFIX + path
            resp = client.get_object(Bucket=_S3_BUCKET, Key=key)
            return resp["Body"].read().decode("utf-8")
        except client.exceptions.NoSuchKey:
            return None
        except Exception as e:
            log.warning("S3 read failed for %s: %s", path, e)
            return None
    # No S3 — return stub
    if path not in STAKEHOLDER_DOCS:
        return None
    return f"# {_label(path)}\n\n> Storage bucket not connected. Deploy to Railway to view and edit docs.\n"


def write_doc(path: str, content: str) -> bool:
    """Write markdown content to S3 bucket."""
    client = _get_client()
    if not client:
        return False
    try:
        key = _PREFIX + path
        client.put_object(
            Bucket=_S3_BUCKET,
            Key=key,
            Body=content.encode("utf-8"),
            ContentType="text/markdown",
        )
        return True
    except Exception as e:
        log.warning("S3 write failed for %s: %s", path, e)
        return False


def _label(path: str) -> str:
    """Extract a human label from a doc path."""
    name = path.rsplit("/", 1)[-1].replace(".md", "")
    return name.replace("-", " ").replace("_", " ").title()
