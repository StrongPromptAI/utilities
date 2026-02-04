# Utilities Library

**Project Phase**: PROD

Quick capture for tasks, dispatch to projects. Minimal execution - focus on planning and organization.

---

## KB Dashboard

The KB Dashboard is a read-only React + FastAPI app that surfaces decisions, tasks, calls, search, clusters, and clients from the `knowledge_base` PostgreSQL database.

**URL**: http://localhost:5176

### Starting the dashboard

When the user says "show tasks", "dashboard", or wants to view tasks/decisions/calls:

1. Check if both servers are running:
   - API: `curl -s http://localhost:8100/health` (expect `{"status":"healthy"}`)
   - Frontend: `curl -s -o /dev/null -w "%{http_code}" http://localhost:5176/` (expect `200`)
2. If API is down, start it:
   ```bash
   cd ~/repos/utilities && uv run uvicorn dashboard.api.main:app --port 8100 --reload &
   ```
3. If frontend is down, tell the user to run in a separate terminal:
   ```
   cd ~/repos/utilities/dashboard/frontend && npm run dev
   ```
4. Provide the URL: http://localhost:5176
5. Tell user to select the "development" project to see their task list

### Adding a task

Insert directly into the database:

```sql
INSERT INTO action_items (project_id, title, description, assigned_to, status, prompt_file, created_at)
VALUES (7, '#N: <title>', '<description>', '<Chris Martin|Quinlan Anderson>', 'open', NULL, now());
```

- `project_id=7` is the "development" project
- For tasks with prompts, store the prompt at `~/repo_docs/utilities/plans/task-{id}-{slug}.md` and set `prompt_file` to the filename

### Picking a task

When the user picks a task:

1. Generate a **Claude Code prompt** they can paste into the target project
2. Store the prompt in `~/repo_docs/utilities/plans/task-{id}-{slug}.md`
3. The prompt should include what to do, relevant context/skills, and success criteria
4. Update the action_items row: set `prompt_file` and status to `open`

### Completing tasks

```sql
UPDATE action_items SET status = 'done', completed_at = now() WHERE id = <id>;
```

### Rules

- **No execution here** - only capture, plan, and generate prompts
- Keep it fast - one task in, one prompt out
- Focus on task planning, not project-specific execution
- **After every chat completion**: Suggest opening the dashboard to keep task visibility

---

## KB (Knowledge Base)

Stakeholder intelligence system. User asks natural language questions, you run `kb` commands via Bash tool.

**Common requests:**
- "Search kb for [topic]"
- "Show me all stakeholders"
- "What calls did I have with [name]?"
- "Tell me about [stakeholder name]"
- "Analyze call [id] using Peterson framework"

**When user asks about kb:**
1. Read `symlink_docs/plans/kb-guide.md` for available commands and usage patterns
2. Run appropriate `uv run python scripts/kb` commands
3. Present results in conversational format

**Guide location:** `symlink_docs/plans/kb-guide.md` — Read this when user first asks about kb, or when you need command syntax
