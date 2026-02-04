"""All GET endpoints for the KB Dashboard."""

import os
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse

from scripts.kb_core.db import get_db
from scripts.kb_core.search import semantic_search, get_client_context
from scripts.kb_core.clustering import get_cluster_details, expand_by_cluster

PROMPTS_DIR = Path(os.path.expanduser("~/repo_docs/utilities/plans"))

router = APIRouter()

# Stop words for cluster labeling — standard + transcript filler
_STOP = frozenset(
    # Standard English stop words
    "i me my we our you your he she it they them their its a an the and but or "
    "so if in on at to for of is am are was were be been being have has had do "
    "does did will would shall should can could may might must not no nor that "
    "this these those what which who whom how when where why all any each every "
    "some much many more most other such than too very just also about after "
    "before between from into through during with without again further then "
    "once here there up down out off over under above below like well really "
    # Conversational filler and transcript artifacts
    "going gonna kind mean maybe sort like yeah right okay sure well actually "
    "thing things people said say says know think believe guess stuff basically "
    "literally probably definitely certainly perhaps obviously certainly clearly "
    "pretty much anyway though however still already even ever never always "
    "want need make made way getting come came went goes take took look looking "
    "tell told talk talking asking asked give gave done doing trying tried "
    "good great nice fine okay cool awesome interesting different little "
    "whole bunch couple able point part question answer something anything "
    "nothing everything else another first last next back long "
    "start started keep kept feel felt seems seemed work working worked "
    "really truly honestly frankly simply exactly happen happened "
    "chris kevin john jeff sara bawa".split()
)


def _cluster_label(chunks: list[dict], max_words: int = 3) -> str:
    """Generate a short descriptive label from chunk texts using top keywords.

    Filters filler/stop words aggressively and requires min 4 chars.
    Uses document frequency (how many chunks contain the word) rather than
    raw count, so words that appear across chunks rank higher.
    """
    from collections import Counter

    doc_freq: Counter[str] = Counter()
    for ch in chunks:
        words = ch.get("text", "").lower().split()
        seen: set[str] = set()
        for w in words:
            cleaned = w.strip(".,;:!?\"'()-/[]{}#@$%^&*_+=~`<>|\\")
            if len(cleaned) > 3 and cleaned not in _STOP and cleaned.isalpha() and cleaned not in seen:
                seen.add(cleaned)
                doc_freq[cleaned] += 1

    top = [w for w, _ in doc_freq.most_common(max_words)]
    return " / ".join(top) if top else "unnamed"


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
def list_projects():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM projects ORDER BY name")
            return _serialize(cur.fetchall())


# --- Decisions ---

@router.get("/projects/{project_id}/decisions")
def list_decisions(project_id: int, status: Optional[str] = None):
    query = "SELECT * FROM decisions WHERE project_id = %s"
    params: list = [project_id]
    if status:
        query += " AND status = %s"
        params.append(status)
    query += " ORDER BY created_at DESC"

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return _serialize(cur.fetchall())


@router.get("/decisions/{decision_id}")
def get_decision(decision_id: int):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM decisions WHERE id = %s", (decision_id,))
            row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Decision not found")
    return _serialize_one(row)


# --- Open Questions ---

@router.get("/projects/{project_id}/questions")
def list_questions(project_id: int, status: Optional[str] = None):
    query = "SELECT * FROM open_questions WHERE project_id = %s"
    params: list = [project_id]
    if status:
        query += " AND status = %s"
        params.append(status)
    query += " ORDER BY created_at DESC"

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return _serialize(cur.fetchall())


@router.get("/questions/{question_id}")
def get_question(question_id: int):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM open_questions WHERE id = %s", (question_id,))
            row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Question not found")
    return _serialize_one(row)


# --- Action Items ---

@router.get("/projects/{project_id}/actions")
def list_actions(project_id: int, status: Optional[str] = None):
    query = """
        SELECT a.*, d.topic as decision_topic, d.status as decision_status
        FROM action_items a
        LEFT JOIN decisions d ON a.decision_id = d.id
        WHERE a.project_id = %s
    """
    params: list = [project_id]
    if status:
        query += " AND a.status = %s"
        params.append(status)
    query += " ORDER BY a.status = 'done', a.created_at DESC"

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return _serialize(cur.fetchall())


@router.get("/actions/{action_id}")
def get_action(action_id: int):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT a.*, d.topic as decision_topic, d.status as decision_status
                   FROM action_items a
                   LEFT JOIN decisions d ON a.decision_id = d.id
                   WHERE a.id = %s""",
                (action_id,),
            )
            row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Action item not found")
    return _serialize_one(row)


@router.get("/actions/{action_id}/prompt")
def get_action_prompt(action_id: int):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT prompt_file FROM action_items WHERE id = %s",
                (action_id,),
            )
            row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Action item not found")
    if not row["prompt_file"]:
        raise HTTPException(404, "No prompt file for this task")

    path = PROMPTS_DIR / row["prompt_file"]
    if not path.exists():
        raise HTTPException(404, f"Prompt file not found: {row['prompt_file']}")
    return PlainTextResponse(path.read_text())


# --- Calls ---

@router.get("/calls")
def list_calls(
    project_id: Optional[int] = None,
    limit: int = Query(default=20, le=100),
):
    query = """
        SELECT c.id, c.call_date, c.source_type, c.summary, c.user_notes,
               cl.name as client_name, p.name as project_name
        FROM calls c
        JOIN clients cl ON c.client_id = cl.id
        LEFT JOIN projects p ON c.project_id = p.id
    """
    params: list = []
    if project_id:
        query += " WHERE c.project_id = %s"
        params.append(project_id)
    query += " ORDER BY c.call_date DESC LIMIT %s"
    params.append(limit)

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return _serialize(cur.fetchall())


@router.get("/calls/{call_id}")
def get_call(call_id: int):
    with get_db() as conn:
        with conn.cursor() as cur:
            # Call + client + project
            cur.execute(
                """SELECT c.*, cl.name as client_name, p.name as project_name
                   FROM calls c
                   JOIN clients cl ON c.client_id = cl.id
                   LEFT JOIN projects p ON c.project_id = p.id
                   WHERE c.id = %s""",
                (call_id,),
            )
            call = cur.fetchone()
            if not call:
                raise HTTPException(404, "Call not found")

            # Participants
            cur.execute(
                """SELECT * FROM participants WHERE call_id = %s ORDER BY id""",
                (call_id,),
            )
            participants = cur.fetchall()

            # Batch summaries
            cur.execute(
                """SELECT * FROM chunk_batch_summaries WHERE call_id = %s ORDER BY batch_idx""",
                (call_id,),
            )
            summaries = cur.fetchall()

            # Chunks
            cur.execute(
                """SELECT id, chunk_idx, text, speaker FROM chunks
                   WHERE call_id = %s ORDER BY chunk_idx""",
                (call_id,),
            )
            chunks = cur.fetchall()

    return {
        "call": _serialize_one(call),
        "participants": _serialize(participants),
        "summaries": _serialize(summaries),
        "chunks": _serialize(chunks),
    }


# --- Clients ---

@router.get("/clients")
def list_clients():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM clients ORDER BY name")
            return _serialize(cur.fetchall())


@router.get("/clients/{name}")
def get_client(name: str):
    result = get_client_context(name)
    if "error" in result:
        raise HTTPException(404, result["error"])
    # Serialize nested structures
    result["client"] = _serialize_one(result["client"])
    result["calls"] = _serialize(result["calls"])
    return result


# --- Search ---

@router.get("/search")
def search(
    q: str = Query(..., min_length=1),
    client: Optional[str] = None,
    limit: int = Query(default=10, le=50),
):
    results = semantic_search(q, client_name=client, limit=limit)
    return _serialize(results)


@router.get("/search/expand")
def search_expand(
    chunk_ids: str = Query(..., description="Comma-separated chunk IDs"),
):
    ids = [int(x.strip()) for x in chunk_ids.split(",") if x.strip()]
    if not ids:
        return []
    results = expand_by_cluster(ids)
    return _serialize(results)


# --- Clusters ---

@router.get("/clusters")
def list_clusters(
    call_id: Optional[int] = None,
    min_size: int = Query(default=2, ge=1),
):
    results = get_cluster_details(call_id=call_id, min_size=min_size)
    for cluster in results:
        cluster["label"] = _cluster_label(cluster["chunks"])
        cluster["chunks"] = _serialize(cluster["chunks"])
    return results
