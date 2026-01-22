# Utilities Library

Quick capture for tasks, dispatch to projects. Minimal execution - focus on planning and organization.

## Adding a task

When the user shares a task, idea, or todo:

1. Read `tasks.json`
2. Add entry with:
   - `id`: increment from last (or start at 1)
   - `description`: the task description
   - `project`: project name if mentioned (optional)
   - `created`: ISO timestamp
   - `status`: "open"
   - `resource`: "Q" (Quinland) or "C" (Chris) - defaults to "C" if not specified
3. Write the updated file
4. Confirm briefly: "Captured: [description]"

## Viewing tasks

Show the list in a clean format. Ask which one they want to work on.

## Picking a task

When the user picks a task:

1. Generate a **Claude Code prompt** they can paste into the target project
2. The prompt should include:
   - What to do (the task, expanded if needed)
   - Any relevant context or skills to use
   - Clear success criteria
3. Mark the task status as "in_progress"

## Completing tasks

When user says they finished something, mark status as "done".

## Rules

- **No execution here** - only capture, plan, and generate prompts
- Keep it fast - one task in, one prompt out
- Don't let the user get sucked into doing work in this repo
- Focus on task planning, not project-specific execution

## Claude Code Setup

### Verify Symlinks

If skills aren't loading or you see "file not found" errors:

```bash
file .claude/rules/uv.md .claude/rules/golden-stack.md
ls .claude/skills/
file symlink_docs
```

If broken or missing, ask: **"Help me fix Claude Code symlinks"**

### Verify Gitignore

Before using symlinks, ensure `.gitignore` has these entries:

```bash
grep -E "\.claude/rules|\.claude/skills|symlink_docs" .gitignore
```

### Rules (Always Loaded)

- **uv.md** - Use `uv run`, not `python`
- **golden-stack.md** - 6 core architecture principles

### Skills (On-Demand)

Symlinked at `.claude/skills/NAME.md`. Load with `@.claude/skills/NAME.md` when needed.

**Full skills index**: `~/repo_docs/CLAUDE.md` (check for latest available skills)

| Skill | Subskills | Trigger Keywords | When to Use |
|-------|-----------|------------------|-------------|
| **auth** | otp-fastmail-delivery | login, otp, session | User authentication, login flows |
| **chat** | — | llm, streaming, response | LLM-powered features |
| **demo-arch** | — | architecture, lm studio | Local development architecture |
| **fast-api** | — | api, endpoints, cors | API endpoints, CORS |
| **planning** | review | architecture, phases | Feature planning, phases |
| **postgres** | pg_dump, mcp-dbhub, semantic-hybrid-search, singleton | database, schema, rag | Data pipelines, RAG search |
| **project-setup** | — | symlinks, claude.md | Project initialization |
| **push** | — | deploy, release | Deploy utilities library |
| **pwa** | — | offline, installable | Progressive Web App patterns |
| **search** | — | rag, embeddings | RAG search, embeddings |
| **skill-curation** | — | skill, rule, update | Skill and rule updates |
| **utilities** | show-tasks | tasks, open, view | Show open tasks, task management |
| **versioning** | — | version, semver, tag | Git tags, releases |
| **voice-api** | — | voice, deepgram, stt | Voice input, Deepgram STT |

### Project Documentation

All planning documents go in `symlink_docs/plans/`, which symlinks to `~/repo_docs/utilities/plans/`.
