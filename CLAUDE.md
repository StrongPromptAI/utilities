# Utilities Library

**Project Phase**: PROD

Quick capture for tasks, dispatch to projects. Minimal execution - focus on planning and organization.

---

## KB Dashboard

The KB Dashboard is a read-only React + FastAPI app that surfaces decisions, tasks, calls, search, clusters, and clients from the `knowledge_base` PostgreSQL database.

**URL**: http://localhost:5176

### Starting the dashboard

When the user says "show tasks", "dashboard", or wants to view tasks/decisions/calls:

Port assignments: see `~/repo_docs/PORT_REGISTRY.md` (backend 8006, frontend 3006)

1. Check if both servers are running:
   - API: `curl -s http://localhost:8006/health` (expect `{"service":"kb-dashboard"}`)
   - Frontend: `curl -s -o /dev/null -w "%{http_code}" http://localhost:3006/` (expect `200`)
2. If API is down, start it:
   ```bash
   cd ~/repos/utilities && uv run uvicorn dashboard.api.main:app --port 8006 --reload &
   ```
3. If frontend is down, tell the user to run in a separate terminal:
   ```
   cd ~/repos/utilities/dashboard/frontend && npm run dev
   ```
4. Provide the URL: http://localhost:3006
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

## Railway

**API only — never use the Railway CLI.** The CLI session cookie (`rw_Fe26.*`) expires constantly and `railway login` requires interactive auth. Use the GraphQL API with the account-level API token.

**Token location**: `~/.railway/config.json` → `user.apiToken` (UUID format)

```bash
# Read token
TOKEN=$(python3 -c "import json; print(json.load(open('$HOME/.railway/config.json'))['user']['apiToken'])")

# Test auth
curl -s -X POST https://backboard.railway.com/graphql/v2 \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"query { me { email name } }"}'
```

**Project IDs (production)**:
- Project: `a3677be5-5392-473e-b609-f23b7c06b78c`
- Environment: `3317309b-8f0c-43f4-9d8a-73b1c9fecf9c`
- hj-roadmap service: `5d97ef67-1434-487a-9069-df8b98a0dd95`
- Postgres service: `ae33aa6f-3890-4af7-aec6-13904be1c242`
- Workspace: `ddd86c61-bd3b-4316-9f5c-d44541c66cc3`

**Domain**: `roadmap.pop.clinic`

**Pull API vars**:
```bash
TOKEN=$(python3 -c "import json; print(json.load(open('$HOME/.railway/config.json'))['user']['apiToken'])")
curl -s -X POST https://backboard.railway.com/graphql/v2 \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"query":"query { variables(projectId: \"a3677be5-5392-473e-b609-f23b7c06b78c\", environmentId: \"3317309b-8f0c-43f4-9d8a-73b1c9fecf9c\", serviceId: \"5d97ef67-1434-487a-9069-df8b98a0dd95\") }"}'
```

**If token is missing**: User must create a new one at railway.com/account/tokens (account-level, NOT workspace-scoped) and write it to `~/.railway/config.json` → `user.apiToken`.

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
