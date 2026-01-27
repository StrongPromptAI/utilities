"""Project CRUD operations."""

from typing import Optional
from pathlib import Path
from ..db import get_db


def get_project(name: str) -> Optional[dict]:
    """Get project by name."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM projects WHERE name = %s", (name,))
            return cur.fetchone()


def list_projects() -> list[dict]:
    """List all projects."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM projects ORDER BY name")
            return cur.fetchall()


def get_project_docs(project_name: str) -> dict:
    """Load project documentation files from {repo_path}/project/.

    repo_path is now the docs path (e.g., ~/repo_docs/itherapeutics),
    not the code repo path.
    """
    project = get_project(project_name)
    if not project:
        return {"error": f"Project '{project_name}' not found"}

    docs_root = Path(project["repo_path"])
    project_docs_dir = docs_root / "project"
    docs = {"project_name": project_name, "docs_path": str(docs_root)}

    # Standard doc files
    doc_files = {
        "context": "PROJECT_CONTEXT.md",
        "prd": "PRD.md",
        "db": "PROJECT_DB.md"
    }

    for doc_type, filename in doc_files.items():
        path = project_docs_dir / filename
        if path.exists():
            docs[doc_type] = path.read_text()
        else:
            docs[doc_type] = None  # File not found

    return docs
