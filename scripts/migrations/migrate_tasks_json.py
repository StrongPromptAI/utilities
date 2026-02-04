"""Migrate tasks.json into action_items table under project 'development' (id=7)."""

import json
import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

TASKS_FILE = Path(__file__).resolve().parents[2] / "tasks.json"
PROMPTS_DIR = Path(os.path.expanduser("~/repo_docs/utilities/plans"))

RESOURCE_MAP = {"C": "Chris Martin", "Q": "Quinlan Anderson"}
# action_items only supports: open, done, cancelled
STATUS_MAP = {"open": "open", "in_progress": "open", "done": "done"}

DEVELOPMENT_PROJECT_ID = 7


def find_prompt_file(task_id: int) -> str | None:
    """Find a prompt file matching task-{id}-*.md pattern."""
    for f in PROMPTS_DIR.glob(f"task-{task_id}-*.md"):
        return f.name
    return None


def main():
    with open(TASKS_FILE) as f:
        tasks = json.load(f)

    conninfo = "host=localhost port=5433 dbname=knowledge_base user=postgres password=postgres"
    with psycopg.connect(conninfo, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            # Check which legacy IDs already exist (avoid duplicates on re-run)
            cur.execute(
                "SELECT title FROM action_items WHERE project_id = %s",
                (DEVELOPMENT_PROJECT_ID,),
            )
            existing_titles = {row["title"] for row in cur.fetchall()}

            inserted = 0
            skipped = 0
            for task in tasks:
                title = f"#{task['id']}: {task['description'][:120]}"
                if title in existing_titles:
                    skipped += 1
                    continue

                status = STATUS_MAP.get(task["status"], "open")
                assigned_to = RESOURCE_MAP.get(task.get("resource", ""), None)
                prompt_file = find_prompt_file(task["id"])

                # Build description with project context
                desc_parts = []
                if task.get("project"):
                    desc_parts.append(f"Project: {task['project']}")
                desc_parts.append(task["description"])
                description = "\n".join(desc_parts)

                completed_at = None
                if status == "done" and task.get("created"):
                    completed_at = task["created"]

                cur.execute(
                    """INSERT INTO action_items
                       (project_id, title, description, assigned_to, status,
                        prompt_file, created_at, completed_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        DEVELOPMENT_PROJECT_ID,
                        title,
                        description,
                        assigned_to,
                        status,
                        prompt_file,
                        task.get("created"),
                        completed_at,
                    ),
                )
                inserted += 1

            conn.commit()
            print(f"Inserted {inserted} tasks, skipped {skipped} duplicates")


if __name__ == "__main__":
    main()
