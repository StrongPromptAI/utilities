# Project Management Utility

**Version**: 1.1.0
**Status**: Active (consolidated from ~/repos/pm)
**Last Updated**: 2026-01-10T20:15:00-07:00

---

## Purpose

Lightweight, JSON-based personal project manager that captures work-in-progress thoughts, par-bakes them through light research, then dispatches to execution. Provides a feedback loop: **Work → Errors → Patterns → Skill Gaps → Better Guidance.**

---

## The Problem

When you're deep in a Claude Code session, you often encounter secondary tasks, improvements, or patterns worth exploring—but switching contexts to track them breaks your flow. You need a quick way to capture thoughts mid-project, research the approach without committing to execution, and organize them for later.

---

## The Solution

**Project Management Utility** is a minimal CLI-first PM system that:

- ✓ Captures thoughts in seconds (no UI, no friction)
- ✓ Stores tasks in `thoughts.json` (JSON is source of truth)
- ✓ **Par-bakes tasks**: light research before execution (30-60 min per task)
- ✓ Generates detailed Claude Code prompts with research context
- ✓ Dispatches tasks to specific projects
- ✓ Tracks task lifecycle: `open` → `par_baked` → `in_progress` → `done`
- ✓ Aggregates error patterns via error-logging utility
- ✓ Stays out of your way (you're the PM, Claude executes)

---

## Core File: thoughts.json

Central task database. Each task entry has:

```json
{
  "id": 1,
  "thought": "Implement OTP authentication for patient portal",
  "project": "itheraputix",
  "created": "2026-01-09T19:25:00-07:00",
  "par_baked_at": "2026-01-10T11:30:00-07:00",
  "completed": "2026-01-10T15:45:00-07:00",
  "status": "done",
  "priority": "high",
  "approach": "OTP via EmailJS + Postgres login_codes table + 6-minute expiry",
  "research_notes": "Considered: password-based (rejected: HIPAA complexity), OAuth (rejected: vendor lock-in, no SMS). OTP + EmailJS: simplest, no backend email config. Pattern: see pt-assistant-v6.",
  "skills": ["auth", "postgres", "fast-api"],
  "prompt": "Implement OTP authentication...\n\nContext from research: OTP via EmailJS...",
  "blockers": "",
  "recovery": ""
}
```

### Fields

| Field | Required | Type | Purpose |
|-------|----------|------|---------|
| `id` | Yes | Integer | Unique identifier (auto-increment) |
| `thought` | Yes | String | One-line task description |
| `project` | No | String | Which repo/project this belongs to |
| `created` | Yes | String | ISO 8601 timestamp when captured |
| `par_baked_at` | No | String | ISO 8601 timestamp when research completed |
| `completed` | No | String | ISO 8601 timestamp when done |
| `status` | Yes | String | `open`, `par_baked`, `in_progress`, or `done` |
| `priority` | No | String | `high`, `medium`, `low` |
| `approach` | No | String | Recommended strategy/architecture decision |
| `research_notes` | No | String | Findings from light research phase |
| `skills` | No | Array | Relevant skill tags for this task |
| `prompt` | No | String | Detailed Claude Code prompt for execution |
| `blockers` | No | String | Known blockers or dependencies |
| `recovery` | No | String | How it was resolved (if completed) |

---

## Task Lifecycle

```
┌─────────────────────────────────────────────────────────────────┐
│                      TASK LIFECYCLE                             │
└─────────────────────────────────────────────────────────────────┘

1. CAPTURE (open)
   └─ Quick one-liner while in flow
   └─ No research yet, just capture
   └─ 30 seconds

2. PRIORITIZE
   └─ Ruthlessly filter tasks
   └─ Keep only essential, high-impact items
   └─ 5 minutes (weekly)

3. PAR-BAKE (par_baked)
   └─ Light research on approach
   └─ Document findings in research_notes
   └─ Generate detailed prompt with context
   └─ 30-60 minutes per task
   └─ Status: ready for execution

4. EXECUTE (in_progress)
   └─ Hand to Claude Code with researched prompt
   └─ Execute in project repo
   └─ Run in project repo, not PM repo
   └─ Hours/days depending on task

5. COMPLETE (done)
   └─ Mark status done, add completed timestamp
   └─ Store recovery notes (if issues hit)
   └─ Update error patterns if applicable
```

---

## Workflows

### 1. Capturing Thoughts (No Research Yet)

**Scenario**: You're working in `lora_training`, spot an improvement, don't want to lose focus.

```bash
# Edit thoughts.json, add entry with minimal info

{
  "id": 42,
  "thought": "Add data validation to training script",
  "project": "lora_training",
  "created": "2026-01-10T20:30:00-07:00",
  "status": "open"
}

# Save, close, back to work (30 seconds total)
```

**Key**: No research, no approach decision. Just capture and move on.

---

### 2. Par-Baking: Light Research Phase (30-60 min)

**Later, when ready to work on tasks**, pull from `open` pool and par-bake:

```bash
# Get all open tasks, prioritize
cat ~/repos/utilities/project-management/thoughts.json | jq '.[] | select(.status=="open")'

# Pick task #42, research the approach
# Questions to answer:
# - What's the best way to do this?
# - What context does Claude need?
# - Are there existing examples in repo_docs/skills?
# - What could go wrong?
# - Any dependencies or blockers?
```

**Example Par-Baking: OTP Authentication**

```json
{
  "id": 1,
  "thought": "Implement OTP authentication for patient portal",
  "project": "itheraputix",
  "created": "2026-01-09T19:25:00-07:00",
  "status": "open"
}

// ─── After 45 minutes of research ───

{
  "id": 1,
  "thought": "Implement OTP authentication for patient portal",
  "project": "itheraputix",
  "created": "2026-01-09T19:25:00-07:00",
  "par_baked_at": "2026-01-10T11:30:00-07:00",
  "status": "par_baked",
  "priority": "high",
  "approach": "OTP via EmailJS + Postgres login_codes table (code + expiry_at) + 6-minute expiry window",
  "research_notes": "Evaluated approaches: (1) Password-based auth rejected (HIPAA compliance overhead). (2) OAuth providers rejected (vendor lock-in, no SMS). (3) OTP chosen: simplest, no backend email config required. EmailJS handles SMTP. Reference: pt-assistant-v6 has working pattern. Store otp_code + created_at in Postgres, not emails. Expiry check on verify endpoint.",
  "skills": ["auth", "postgres", "fast-api"],
  "blockers": "EmailJS API key needs to be added to Railway secrets",
  "prompt": "Implement OTP-based authentication...\n\nContext from research:\n- Use EmailJS for email delivery (no backend SMTP)\n- Schema: login_codes(email, otp_code, created_at, expires_at)\n- Flow: POST /auth/login → generate OTP → send via EmailJS → POST /auth/verify-otp with OTP code\n- Expiry: 6 minutes\n- Reference existing pattern: pt-assistant-v6\n\nDetails: ...",
  "prompt": "..."
}
```

**Par-Baking Steps**:

1. **Define the problem** - What exactly are we building?
2. **Research options** - What are 2-3 approaches? Pros/cons?
3. **Choose approach** - Which is best for our constraints?
4. **Find examples** - Existing code/skills/patterns to reference?
5. **Identify blockers** - Dependencies, API keys, design decisions?
6. **Draft prompt** - What would Claude need to execute this?
7. **Update task** - Set status to `par_baked`, document everything

---

### 3. Par-Baked Tasks Ready for Execution

```bash
# View all par-baked tasks (ready to execute)
cat ~/repos/utilities/project-management/thoughts.json | jq '.[] | select(.status=="par_baked")'

# Get the prompt for task #1
cat ~/repos/utilities/project-management/thoughts.json | jq '.[] | select(.id==1).prompt' | pbcopy

# Open Claude Code in itheraputix project, paste prompt, execute
```

**Before**:
- Task was vague, required Claude to research approach
- Wastes AI tokens on exploration instead of execution
- Risk of misaligned approach

**After par-baking**:
- Approach is researched, documented, ready
- Claude gets detailed context upfront
- Faster execution, better results

---

### 4. Execution (in_progress)

```bash
# Edit thoughts.json: mark as in_progress
{
  "id": 1,
  "status": "in_progress",
  "...": "..."
}

# Open Claude Code in project repo
# Paste researched prompt from thoughts.json
# Execute task there (not in PM repo)

# If issues hit:
# - Update recovery field in thoughts.json
# - Patterns → create error analysis task
```

---

### 5. Completion (done)

```bash
# After task completes successfully
{
  "id": 1,
  "status": "done",
  "completed": "2026-01-10T15:45:00-07:00",
  "recovery": ""  # or notes if issues required workaround
}

# Or if unexpected issues:
{
  "id": 1,
  "status": "done",
  "completed": "2026-01-10T15:45:00-07:00",
  "recovery": "EmailJS rate limiting hit during testing. Solution: increased tier to Pro. Consider adding retry logic in future."
}
```

---

## Par-Baking Examples

### Example 1: Feature Implementation

**Captured** (open):
```json
{
  "id": 10,
  "thought": "Add dark mode toggle to web app",
  "project": "Itheraputix-Web",
  "created": "2026-01-09T14:20:00-07:00",
  "status": "open"
}
```

**Par-Baked** (after research):
```json
{
  "id": 10,
  "thought": "Add dark mode toggle to web app",
  "project": "Itheraputix-Web",
  "created": "2026-01-09T14:20:00-07:00",
  "par_baked_at": "2026-01-10T10:15:00-07:00",
  "status": "par_baked",
  "priority": "medium",
  "approach": "React Context + localStorage for persistence. CSS-in-JS (Tailwind dark: modifier) for styling. Toggle in header, applies globally.",
  "research_notes": "Options: (1) CSS media prefers-color-scheme (rejected: ignores user preference). (2) Context + localStorage (chosen: user control, persistence). (3) CSS-in-JS library (rejected: overkill). Implementation: createThemeContext → useTheme hook → wrap App → toggle button updates context → Tailwind dark: applies. Reference: Next.js docs on dark mode.",
  "skills": ["react", "tailwind"],
  "blockers": "None",
  "prompt": "Add dark mode toggle to React app...\n\nApproach: Use React Context + localStorage...\n\nImplementation plan:\n1. Create ThemeContext\n2. Add useTheme hook\n3. Wrap App with provider\n4. Add toggle button in header\n5. Apply Tailwind dark: classes to components\n6. Test persistence on page reload\n\nReference: Next.js dark mode documentation"
}
```

---

### Example 2: Bug Fix

**Captured** (open):
```json
{
  "id": 15,
  "thought": "Session timeout not working on mobile",
  "project": "itheraputix_react_native",
  "created": "2026-01-08T16:45:00-07:00",
  "status": "open"
}
```

**Par-Baked** (after investigation):
```json
{
  "id": 15,
  "thought": "Session timeout not working on mobile",
  "project": "itheraputix_react_native",
  "created": "2026-01-08T16:45:00-07:00",
  "par_baked_at": "2026-01-10T09:30:00-07:00",
  "status": "par_baked",
  "priority": "high",
  "approach": "Root cause: React Native AsyncStorage doesn't clear on app background. Solution: Add AppState listener to check token expiry when app resumes.",
  "research_notes": "Investigated: Session expires server-side after 24h. Client doesn't know until next API call. On web, tab refresh resets timers. On mobile, app stays in memory. Solution: Use AppState listener (AppState.addEventListener) to check token validity when app moves to foreground. If expired, redirect to login. Reference: React Native docs on app state, pt-assistant-v6 session handling.",
  "skills": ["react-native", "auth"],
  "blockers": "None",
  "prompt": "Fix session timeout on mobile...\n\nRoot cause: AppState doesn't trigger when app resumes...\n\nImplementation:\n1. Import AppState from React Native\n2. Add useEffect with AppState listener\n3. On foreground: check token expiry (JWT decode)\n4. If expired: clear AsyncStorage + redirect to login\n5. Test: background app 30 seconds, resume, verify redirect\n\nReference: pt-assistant-v6 session code, React Native AppState docs"
}
```

---

### Example 3: Infrastructure Task

**Captured** (open):
```json
{
  "id": 8,
  "thought": "Set up staging environment on Railway",
  "project": "mainstreak",
  "created": "2026-01-09T10:00:00-07:00",
  "status": "open"
}
```

**Par-Baked** (after planning):
```json
{
  "id": 8,
  "thought": "Set up staging environment on Railway",
  "project": "mainstreak",
  "created": "2026-01-09T10:00:00-07:00",
  "par_baked_at": "2026-01-10T14:00:00-07:00",
  "status": "par_baked",
  "priority": "high",
  "approach": "Create new Railway project 'mainstreak-staging'. Copy production services (PostgreSQL, FastAPI backend, React frontend). Use different domain: staging.mainstreak.dev. Environment variables from Railway template, secrets from vault.",
  "research_notes": "Reviewed: Railway project structure. Staging setup: new project, not separate environment. Clone services: PostgreSQL data (migrate schemas only), backend + frontend from main branch. DNS: staging.mainstreak.dev → Railway domain. Env vars: DATABASE_URL (new staging DB), API_URL (staging backend). Cost: ~$20/month extra. Reference: railway/ skill, push/environment.md",
  "skills": ["railway", "push"],
  "blockers": "Need domain routing setup (DNS records). Vault access to pull staging secrets.",
  "prompt": "Set up staging environment on Railway...\n\nGoal: Enable smoke testing before production deploys...\n\nSteps:\n1. Create new Railway project: 'mainstreak-staging'\n2. Set up PostgreSQL: new database, migrate schema from prod\n3. Deploy backend: from main branch, configure staging env vars\n4. Deploy frontend: from main branch, set API_URL to staging backend\n5. Add domain: staging.mainstreak.dev (coordinate with DevOps)\n6. Test: deploy to staging first, smoke tests, then promote to prod\n\nReference: railway/ skill, push/environment.md for promotion workflow"
}
```

---

## Viewing Tasks by Status

```bash
# Open tasks (captured, not researched)
cat ~/repos/utilities/project-management/thoughts.json | jq '.[] | select(.status=="open") | {id, thought, project}'

# Par-baked tasks (researched, ready to execute)
cat ~/repos/utilities/project-management/thoughts.json | jq '.[] | select(.status=="par_baked") | {id, thought, approach, priority}'

# In progress
cat ~/repos/utilities/project-management/thoughts.json | jq '.[] | select(.status=="in_progress")'

# Completed
cat ~/repos/utilities/project-management/thoughts.json | jq '.[] | select(.status=="done") | {id, thought, completed, recovery}'

# High priority open + par-baked
cat ~/repos/utilities/project-management/thoughts.json | jq '.[] | select(((.status=="open" or .status=="par_baked") and .priority=="high")) | {id, thought, status, approach}'
```

---

## Philosophy: Why This Works

**Deliberately minimal**:
- No database (JSON is source of truth, auditable in git)
- No UI (edit files directly or via cat/jq)
- No execution engine (you're the PM, Claude is the executor)
- No context switching (quick capture, then back to work)
- No automation (intentional—you control prioritization)

**Scales well**:
- Handles 100+ tasks without slowdown
- Git history shows every decision
- Portable: works on any machine with bash/jq
- Versioned: cloneable via utilities repo

**Par-baking saves time**:
- Claude executes with full context (faster)
- Approach is pre-validated (fewer wrong turns)
- Tokens spent on execution, not exploration
- Better results, faster delivery

---

## Best Practices

### Thought Capture

✅ **Do**:
- Capture one-liners (elaborate during par-baking)
- Include project name for routing
- Use ISO 8601 timestamps
- **Prioritize ruthlessly** (not every thought needs a task)
- Return to work immediately

❌ **Don't**:
- Capture every idea (kills the value of prioritization)
- Mix multiple tasks in one thought
- Research or decide approach yet (that's par-baking)
- Leave status undefined

### Prioritization

✅ **Ruthless filtering**:
- Only tasks with clear business value
- Only tasks that unblock other work
- Only tasks you'll execute this month
- Maximum 3-5 high priority tasks

❌ **Anti-patterns**:
- Capturing every improvement idea
- Keeping low-value tasks "just in case"
- Too many open tasks (demoralizing)

### Par-Baking

✅ **Do**:
- Spend 30-60 min researching approach
- Answer: "What's the best way?" before execution
- Find existing examples/patterns
- Document blockers upfront
- Draft execution prompt with full context

❌ **Don't**:
- Skip par-baking and hand to Claude unprepared
- Over-engineer during research (light research only)
- Par-bake tasks you won't execute soon (waste of time)
- Leave research_notes empty

### Task Dispatch

✅ **Do**:
- Only dispatch par_baked tasks to Claude
- Include researched prompt with full context
- Dispatch to correct project repo
- Verify approach with Claude before deep work
- Update status to in_progress when Claude starts

❌ **Don't**:
- Execute tasks in the PM repo (capture only)
- Add execution notes to thoughts.json (use project repo instead)
- Create tasks for others (PM is personal)
- Dispatch unprepared (open → in_progress skips par_baking)

### Error Integration

✅ **Do**:
- Review error queue weekly
- Create tasks for repeated error patterns
- Link error pattern to skill improvement
- Tag new tasks with relevant skills

❌ **Don't**:
- Ignore error patterns
- Create generic "fix errors" tasks (be specific)
- Assume errors are user mistakes (review hook implementation)

---

## Usage from Projects

No symlinks needed. Projects reference utilities directly:

```bash
# In a project, get your next task
cat ~/repos/utilities/project-management/thoughts.json | jq '.[] | select(.project=="this_project" and .status=="par_baked") | .prompt'

# In a project, check errors
cat ~/.error-queue.json | jq '.[] | select(.context | contains("this_project"))'
```

---

## Maintenance

### Weekly Review (30 min)
```bash
# Open tasks
cat ~/repos/utilities/project-management/thoughts.json | jq '.[] | select(.status=="open")' | jq length

# Par-baked ready to execute
cat ~/repos/utilities/project-management/thoughts.json | jq '.[] | select(.status=="par_baked")'

# Errors this week
cat ~/.error-queue.json | jq '.[] | select((.timestamp | fromdateiso8601) > (now - 604800))'

# Decision: prioritize, par-bake, or archive open tasks
```

### Monthly Analysis (1 hour)
```bash
# Completed tasks (velocity)
cat ~/repos/utilities/project-management/thoughts.json | jq '[.[] | select(.status=="done")] | length'

# Completion rate by project
cat ~/repos/utilities/project-management/thoughts.json | jq 'group_by(.project) | map({project: .[0].project, completed: ([.[] | select(.status=="done")] | length), total: length})'

# Error types over time
cat ~/.error-queue.json | jq 'group_by(.error_type) | map({type: .[0].error_type, count: length}) | sort_by(.count) | reverse'

# Skills to improve based on patterns?
```

---

## Troubleshooting

### thoughts.json Not Found

```bash
# Make sure utilities repo is cloned
ls ~/repos/utilities/project-management/thoughts.json

# If missing: clone utilities
git clone https://github.com/StrongPromptAI/utilities.git ~/repos/utilities
```

### Invalid JSON After Edit

```bash
# Validate
jq -e . ~/repos/utilities/project-management/thoughts.json

# If broken: revert from git
cd ~/repos/utilities
git checkout project-management/thoughts.json
```

### Can't Find a Task

```bash
# Search by keyword
cat ~/repos/utilities/project-management/thoughts.json | jq '.[] | select(.thought | contains("keyword"))'

# Search by project
cat ~/repos/utilities/project-management/thoughts.json | jq '.[] | select(.project=="project_name")'

# Search by date
cat ~/repos/utilities/project-management/thoughts.json | jq '.[] | select(.created > "2026-01-01T00:00:00-07:00")'

# Find par-baked tasks ready for execution
cat ~/repos/utilities/project-management/thoughts.json | jq '.[] | select(.status=="par_baked")'
```

---

## Workflow Integration with Error Logging

This utility works best paired with the error-logging utility:

**Error Flow**:
```
Project .claude/hooks/post_tool_error.sh
    ↓ (on error)
~/repos/utilities/error-logging/log-error.sh
    ↓ (appends to)
~/.error-queue.json
    ↓ (weekly review)
Patterns identified
    ↓
Par-bake improvement task
    ↓
Execute task to improve guidance
```

---

## Metrics

- **Current tasks**: `jq 'length' ~/repos/utilities/project-management/thoughts.json`
- **By status**: `jq 'group_by(.status) | map({status: .[0].status, count: length})' ~/repos/utilities/project-management/thoughts.json`
- **Completed this month**: `jq '[.[] | select(.status=="done")] | length' ~/repos/utilities/project-management/thoughts.json`
- **Open per project**: `jq 'group_by(.project) | map({project: .[0].project, count: length})' ~/repos/utilities/project-management/thoughts.json`
- **Par-baked ready**: `jq '[.[] | select(.status=="par_baked")] | length' ~/repos/utilities/project-management/thoughts.json`
- **Average time to complete**: Calculate from `par_baked_at` and `completed` timestamps

---

## Migration from PM Repo

This utility consolidates `~/repos/pm/` functionality into `~/repos/utilities/project-management/`:

**Before**:
```
~/repos/pm/
├── thoughts.json
├── error-queue.json
└── log-error.sh
```

**After (in utilities)**:
```
~/repos/utilities/
├── error-logging/
│   ├── log-error.sh
│   └── SPEC.md
├── project-management/
│   ├── thoughts.json
│   └── SPEC.md
├── VERSION (1.1.0)
└── README.md
```

**Old PM repo**: Now a historical archive. All new work uses utilities library.

---

*Project Management Utility v1.1.0 - 2026-01-10T20:15:00-07:00 | Added par-baking phase with research_notes, approach, and task lifecycle improvements*
