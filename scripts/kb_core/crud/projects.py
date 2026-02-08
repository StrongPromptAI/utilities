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

    Uses standard filenames from kb_config singleton table.
    """
    project = get_project(project_name)
    if not project:
        return {"error": f"Project '{project_name}' not found"}

    if not project.get("repo_path"):
        return {"error": f"Project '{project_name}' has no repo_path set"}

    # Get standard filenames from kb_config
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM kb_config WHERE id = 1")
            config = cur.fetchone()

    docs_root = Path(project["repo_path"]).expanduser()
    # Check symlink_docs/project/ first (standard convention), fall back to project/
    project_docs_dir = docs_root / "symlink_docs" / "project"
    if not project_docs_dir.exists():
        project_docs_dir = docs_root / "project"
    docs = {"project_name": project_name, "docs_path": str(docs_root)}

    doc_files = {
        "project": config["project_file"],
        "architecture": config["architecture_file"],
        "prd": config["prd_file"],
        "restart": config["restart_file"],
    }

    for doc_type, filename in doc_files.items():
        path = project_docs_dir / filename
        if path.exists():
            docs[doc_type] = path.read_text()
        else:
            docs[doc_type] = None

    return docs
