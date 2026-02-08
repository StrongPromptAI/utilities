"""All GET endpoints for the KB Dashboard.

Thin wrapper over kb_core CRUD — no inline SQL here.
"""

import os
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse

from scripts.kb_core.crud.projects import list_projects
from scripts.kb_core.crud.decisions import list_decisions, get_decision
from scripts.kb_core.crud.questions import list_questions, get_open_question
from scripts.kb_core.crud.actions import list_actions, get_action, get_action_prompt_file
from scripts.kb_core.crud.calls import list_calls, get_call_detail
from scripts.kb_core.crud.org import list_org
from scripts.kb_core.crud.contacts import list_contacts
from scripts.kb_core.search import semantic_search, get_org_context
from scripts.kb_core.clustering import get_cluster_details, expand_by_cluster, cluster_label

PROMPTS_DIR = Path(os.path.expanduser("~/repo_docs/utilities/plans"))

router = APIRouter()


def _serialize(rows: list[dict]) -> list[dict]:
    """Convert date/datetime objects to ISO strings for JSON."""
    result = []
    for row in rows:
        out = {}
        for k, v in row.items():
            if isinstance(v, (date, datetime)):
                out[k] = v.isoformat()
            else:
                out[k] = v
        result.append(out)
    return result


def _serialize_one(row: dict | None) -> dict | None:
    if row is None:
        return None
    out = {}
    for k, v in row.items():
        if isinstance(v, (date, datetime)):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


# --- Projects ---

@router.get("/projects")
def api_list_projects():
    return _serialize(list_projects())


# --- Decisions ---

@router.get("/projects/{project_id}/decisions")
def api_list_decisions(project_id: int, status: Optional[str] = None):
    return _serialize(list_decisions(project_id, status=status))


@router.get("/decisions/{decision_id}")
def api_get_decision(decision_id: int):
    row = get_decision(decision_id)
    if not row:
        raise HTTPException(404, "Decision not found")
    return _serialize_one(row)


# --- Open Questions ---

@router.get("/projects/{project_id}/questions")
def api_list_questions(project_id: int, status: Optional[str] = None):
    return _serialize(list_questions(project_id, status=status))


@router.get("/questions/{question_id}")
def api_get_question(question_id: int):
    row = get_open_question(question_id)
    if not row:
        raise HTTPException(404, "Question not found")
    return _serialize_one(row)


# --- Action Items ---

@router.get("/projects/{project_id}/actions")
def api_list_actions(project_id: int, status: Optional[str] = None):
    return _serialize(list_actions(project_id, status=status))


@router.get("/actions/{action_id}")
def api_get_action(action_id: int):
    row = get_action(action_id)
    if not row:
        raise HTTPException(404, "Action item not found")
    return _serialize_one(row)


@router.get("/actions/{action_id}/prompt")
def api_get_action_prompt(action_id: int):
    prompt_file = get_action_prompt_file(action_id)
    if prompt_file is None:
        raise HTTPException(404, "Action item not found or no prompt file")

    path = PROMPTS_DIR / prompt_file
    if not path.exists():
        raise HTTPException(404, f"Prompt file not found: {prompt_file}")
    return PlainTextResponse(path.read_text())


# --- Calls ---

@router.get("/calls")
def api_list_calls(
    project_id: Optional[int] = None,
    limit: int = Query(default=20, le=100),
):
    return _serialize(list_calls(project_id=project_id, limit=limit))


@router.get("/calls/{call_id}")
def api_get_call(call_id: int):
    result = get_call_detail(call_id)
    if not result:
        raise HTTPException(404, "Call not found")
    return {
        "call": _serialize_one(result["call"]),
        "contacts": _serialize(result["contacts"]),
        "summaries": _serialize(result["summaries"]),
        "chunks": _serialize(result["chunks"]),
    }


# --- Orgs ---

@router.get("/orgs")
def api_list_orgs():
    return _serialize(list_org())


@router.get("/orgs/{name}")
def api_get_org(name: str):
    result = get_org_context(name)
    if "error" in result:
        raise HTTPException(404, result["error"])
    result["org"] = _serialize_one(result["org"])
    result["calls"] = _serialize(result["calls"])
    return result


# --- Contacts ---

@router.get("/contacts")
def api_list_contacts():
    return _serialize(list_contacts())


# --- Search ---

@router.get("/search")
def api_search(
    q: str = Query(..., min_length=1),
    client: Optional[str] = None,
    limit: int = Query(default=10, le=50),
):
    results = semantic_search(q, client_name=client, limit=limit)
    return _serialize(results)


@router.get("/search/expand")
def api_search_expand(
    chunk_ids: str = Query(..., description="Comma-separated chunk IDs"),
):
    ids = [int(x.strip()) for x in chunk_ids.split(",") if x.strip()]
    if not ids:
        return []
    results = expand_by_cluster(ids)
    return _serialize(results)


# --- Clusters ---

@router.get("/clusters")
def api_list_clusters(
    call_id: Optional[int] = None,
    min_size: int = Query(default=2, ge=1),
):
    results = get_cluster_details(call_id=call_id, min_size=min_size)
    for c in results:
        c["label"] = cluster_label(c["chunks"])
        c["chunks"] = _serialize(c["chunks"])
    return results
