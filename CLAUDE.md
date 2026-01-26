# Utilities Library

Quick capture for tasks, dispatch to projects. Minimal execution - focus on planning and organization.

**Tasks file location**: `~/repos/utilities/tasks.json` — Always read from here when displaying or updating tasks.

**Prompt file location**: `~/repo_docs/utilities/plans/task-{id}-{slug}.md` — Task prompts stored here (e.g., `task-38-implement-ltp.md`). Check for existing prompts when displaying tasks.

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

Show the list in a clean format. For each task, check if a prompt file exists at `~/repo_docs/utilities/plans/task-{id}-{slug}.md`. If it does, include the path in the output so user can reference it. Ask which one they want to work on.

## Picking a task

When the user picks a task:

1. Generate a **Claude Code prompt** they can paste into the target project
2. Store the prompt in `~/repo_docs/utilities/plans/task-{id}-{slug}.md` (don't bloat tasks.json)
3. The prompt should include:
   - What to do (the task, expanded if needed)
   - Any relevant context or skills to use
   - Clear success criteria
4. Update task description in tasks.json to reference the prompt file: "See prompt: task-{id}-{slug}.md"
5. Mark the task status as "in_progress"

## Completing tasks

When user says they finished something, mark status as "done".

## Rules

- **No execution here** - only capture, plan, and generate prompts
- Keep it fast - one task in, one prompt out
- Don't let the user get sucked into doing work in this repo
- Focus on task planning, not project-specific execution
- **After every chat completion**: Suggest "show tasks" to keep task list visible

## Global Skills

Skills are globally configured via `~/repo_docs/skills/.REGISTRY.md`.

Use any skill by name:
- "use devops" → devops skill loads
- "use postgres" → postgres skill loads
- "use utilities" → utilities skill loads
- "use show-tasks" → utilities/show-tasks subskill loads

**Full skills list**: See `~/repo_docs/skills/.REGISTRY.md`

**If you haven't run global setup yet:**
- See `~/repo_docs/skills/global-setup/SKILL.md` for one-time machine configuration
- After that, all skills work in all projects automatically

---

## Project Documentation

All planning documents go in `symlink_docs/plans/`, which symlinks to `~/repo_docs/utilities/plans/`.
