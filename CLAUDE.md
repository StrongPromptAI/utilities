# Utilities Library

Quick capture for thoughts, dispatch to projects. Minimal execution - focus on planning and organization.

## Adding a thought

When the user shares a thought, idea, or todo about utilities:

1. Read `thoughts.json`
2. Add entry with:
   - `id`: increment from last (or start at 1)
   - `thought`: the user's thought/idea
   - `project`: project name if mentioned (optional)
   - `created`: ISO timestamp
   - `status`: "open"
3. Write the updated file
4. Confirm briefly: "Captured: [thought]"

## Viewing thoughts

Show the list in a clean format. Ask which one they want to work on.

## Picking a task

When the user picks a task:

1. Generate a **Claude Code prompt** they can paste into the target project
2. The prompt should include:
   - What to do (the thought, expanded if needed)
   - Any relevant context or skills to use
   - Clear success criteria
3. Mark the thought status as "in_progress"

## Completing thoughts

When user says they finished something, mark status as "done".

## Rules

- **No execution here** - only capture, plan, and generate prompts
- Keep it fast - one thought in, one prompt out
- Don't let the user get sucked into doing work in this repo
- Focus on utilities planning, not project-specific tasks

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

### Core Rules (Always Loaded)

- **uv.md** - Use `uv run`, not `python`
- **golden-stack.md** - 6 core architecture principles

### Active Skills (On-Demand)

Load with `@.claude/skills/NAME.md` when needed:

| Skill | Use For |
|-------|---------|
| **auth** | Authentication, login, OTP |
| **chat** | LLM, streaming, chat completion |
| **demo-arch** | Architecture, local dev, LM Studio |
| **fast-api** | API endpoints, CORS |
| **planning** | Project planning, phases, dependencies |
| **postgres** | Database, schema, psql |
| **project-setup** | Symlinks and Claude Code configuration |
| **push** | Deploy and release utilities library |
| **pwa** | Offline, installable, Capacitor |
| **search** | RAG, search, embeddings |
| **skill-curation** | Skill and rule updates |
| **versioning** | Version bumps, semantic versioning, git tags |
| **voice-api** | Voice, speech, Deepgram, STT |

### Project Documentation

All planning documents go in `symlink_docs/plans/`, which symlinks to `~/repo_docs/utilities/plans/`.
