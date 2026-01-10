# Utilities Library

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
