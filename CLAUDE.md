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
| **project-setup** | Symlinks and Claude Code configuration |
| **versioning** | Version bumps, semantic versioning, git tags |
| **push** | Deploy and release utilities library |
| **planning** | Planning improvements and features |

### Project Documentation

All planning documents go in `symlink_docs/plans/`, which symlinks to `~/repo_docs/utilities/plans/`.
