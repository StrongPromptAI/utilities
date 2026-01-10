# Utilities Library

Shared utilities and scripts for StrongPrompt projects. Version controlled in GitHub, locally cloned for development, versioned via git tags for production.

**Repository**: https://github.com/StrongPromptAI/utilities

**Local Path**: `~/repos/utilities/`

---

## Quick Start

### Local Development

```bash
# Already cloned to local machine
cd ~/repos/utilities/

# Reference utilities from projects
~/repos/utilities/error-logging/log-error.sh "Tool" "error_type" "message" "context"
```

### Production (CI/CD)

```bash
# Clone specific version tag
git clone --depth 1 --branch v1.0.0 https://github.com/StrongPromptAI/utilities.git /path/to/utilities

# Use versioned utilities
/path/to/utilities/error-logging/log-error.sh "Tool" "error_type" "message" "context"
```

---

## Directory Structure

```
utilities/
├── VERSION                  # Current version (semantic)
├── README.md               # This file
├── .gitignore              # Local artifacts (error-queue.json, etc.)
│
├── error-logging/          # Error capture and logging
│   ├── SPEC.md            # Specification and usage
│   └── log-error.sh       # Main error logging script
│
├── git-helpers/            # Git utilities (planned)
│   └── [future utilities]
│
├── deployment/             # Deployment scripts (planned)
│   └── [future utilities]
│
└── hooks/                  # Claude Code hooks (planned)
    └── [future utilities]
```

---

## Available Utilities

### Error Logging

**Location**: `error-logging/log-error.sh`, `error-logging/SPEC.md`

**Purpose**: Centralized error capture for Claude Code tool failures.

**Usage**:
```bash
~/repos/utilities/error-logging/log-error.sh "Bash" "syntax" "Parse error on line 42" "project: error context"
```

**Output**: Appends to `~/.error-queue.json` with:
- Unique error ID (auto-incrementing)
- ISO 8601 timestamp
- Tool name, error type, message, context

**Status**: ✅ Active (v1.1.0)

### Project Management

**Location**: `project-management/thoughts.json`, `project-management/SPEC.md`

**Purpose**: Lightweight task management with par-baking phase (light research before execution).

**Features**:
- Capture tasks in seconds (no UI friction)
- Par-bake: 30-60 min light research on approach before execution
- Track lifecycle: `open` → `par_baked` → `in_progress` → `done`
- Ruthless prioritization
- Research notes stored for execution clarity

**Usage**:
```bash
# Capture a task (open)
# Edit ~/repos/utilities/project-management/thoughts.json

# View par-baked tasks (ready to execute)
cat ~/repos/utilities/project-management/thoughts.json | jq '.[] | select(.status=="par_baked")'

# Get execution prompt
cat ~/repos/utilities/project-management/thoughts.json | jq '.[] | select(.id==1).prompt'
```

**Status**: ✅ Active (v1.1.0, consolidated from ~/repos/pm)

---

## How Projects Use Utilities (No Symlinks)

Projects reference utilities directly via absolute path:

```bash
# In .claude/hooks/post_tool_error.sh
LOG_ERROR_SCRIPT="$HOME/repos/utilities/error-logging/log-error.sh"

if [ ! -f "$LOG_ERROR_SCRIPT" ]; then
  echo "⚠️  Utilities repo not found. Clone it:"
  echo "  git clone https://github.com/StrongPromptAI/utilities.git ~/repos/utilities"
  exit 0
fi

"$LOG_ERROR_SCRIPT" "$TOOL" "$ERROR_TYPE" "$ERROR_MESSAGE" "$CONTEXT" "" ""
```

**Advantages**:
- No symlink overhead per project
- Single clone, all projects benefit
- Easy to update utilities (just `git pull`)
- Production: Clone specific version tag

---

## Versioning

Utilities follow semantic versioning: `MAJOR.MINOR.PATCH`

### Current Version

Read from `VERSION` file:
```bash
cat ~/repos/utilities/VERSION
# Output: 1.0.0
```

### Release Process

```bash
# Update VERSION file
echo "1.0.1" > VERSION

# Commit
git add .
git commit -m "Release 1.0.1: Fix error-logging edge case"

# Tag release
git tag -a v1.0.1 -m "Release 1.0.1"

# Push
git push origin main --tags
```

---

## Adding New Utilities

1. **Create directory** under appropriate category
2. **Add main script** with error handling (check for dependencies)
3. **Create SPEC.md** with: purpose, usage, dependencies, examples
4. **Test locally** from a project
5. **Commit and tag** new version
6. **Update this README** with new utility info

**Example**: Adding a `git-helpers/auto-commit.sh` utility:

```bash
mkdir -p git-helpers
cat > git-helpers/auto-commit.sh << 'EOF'
#!/bin/bash
# auto-commit.sh - Auto-commit with conventional commit format
# Usage: auto-commit.sh "type: message"
EOF

cat > git-helpers/SPEC.md << 'EOF'
# Git Auto-Commit Utility

## Purpose
Simplify conventional commit creation.

## Usage
git-helpers/auto-commit.sh "feat: add new feature"

## Dependencies
- git

## Exit Codes
- 0: Success
- 1: No message provided
- 2: Git not found
EOF

git add git-helpers/
git commit -m "Add git auto-commit utility"
git tag -a v1.1.0 -m "Release 1.1.0"
git push origin main --tags
```

---

## Dependency Checks

All utilities check for required dependencies and exit gracefully if missing:

```bash
# Good: Check dependency
if ! command -v jq &> /dev/null; then
  echo "⚠️  This utility requires jq. Install with: brew install jq"
  exit 0  # Exit gracefully, don't error
fi
```

---

## Error Handling Conventions

All utilities follow these patterns:

- **Exit 0**: Success or graceful failure (dependency missing)
- **Exit 1**: User error (missing argument, bad input)
- **Exit 2**: System error (file not writable, permission denied)
- **stdout**: Normal output, status messages
- **stderr**: Warnings, diagnostics (sent to `>&2`)

---

## Testing Utilities Locally

```bash
# Clone the utilities repo (if not already present)
git clone https://github.com/StrongPromptAI/utilities.git ~/repos/utilities

# Test a utility
~/repos/utilities/error-logging/log-error.sh "Bash" "test" "Test message" "testing"

# Verify it worked
tail -1 ~/.error-queue.json | jq '.'
```

---

## Production Deployment

In CI/CD pipelines, clone a specific version tag:

```yaml
# .github/workflows/deploy.yml
- name: Clone utilities (v1.0.0)
  run: |
    git clone --depth 1 --branch v1.0.0 \
      https://github.com/StrongPromptAI/utilities.git \
      ${{ runner.temp }}/utilities

    # Use utilities
    ${{ runner.temp }}/utilities/error-logging/log-error.sh "Deploy" "info" "Deployment started" "ci"
```

---

## Contributing

1. Create a utility in appropriate category
2. Add SPEC.md with full documentation
3. Test locally
4. Create PR with description
5. After merge, create version tag: `git tag -a vX.Y.Z -m "Release X.Y.Z: description"`
6. Push tag: `git push origin vX.Y.Z`

---

## GitHub Repository

- **Repository**: https://github.com/StrongPromptAI/utilities
- **Clone**: `git clone https://github.com/StrongPromptAI/utilities.git ~/repos/utilities`
- **Issues**: Report bugs or suggest utilities
- **Releases**: View version history and tags

---

## Maintenance

- **Update utilities locally**: `cd ~/repos/utilities && git pull`
- **Check version**: `cat ~/repos/utilities/VERSION`
- **View recent changes**: `cd ~/repos/utilities && git log --oneline -5`

---

*Utilities Library v1.1.0 - 2026-01-10T20:30:00-07:00 | Error logging + Project management with par-baking*
