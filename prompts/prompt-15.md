Implement a hybrid symlink + git utilities library pattern for shared scripts across projects (following existing symlink_docs + repo_docs/skills patterns).

**Why This Pattern**:
Research shows 5 common approaches:
1. Git submodules - Too complex for bash scripts
2. Pure symlink (like symlink_docs) - Works locally, doesn't scale to production
3. Copy-paste with versions - Maintenance nightmare
4. NPM package - Language-specific, doesn't work for bash
5. **Hybrid symlink + git (RECOMMENDED)** - Version control + local development flexibility

Your existing patterns already use this:
- `symlink_docs/plans/` - Planning docs, symlinked locally, git tracked
- `repo_docs/skills/` - Skills, version controlled, distributed via git
- This utilities library should follow the hybrid: central GitHub repo + local symlink for development

**Goal**: Create a utilities library that:
- Lives in GitHub: `https://github.com/StrongPromptAI/utilities`
- Gets cloned locally to: `~/repos/utilities/`
- Projects symlink/clone during development: `.claude/hooks → ~/repos/utilities`
- Scales to production: CI/CD can clone specific version tags
- Follows your existing patterns (organized by category, documented, versioned)

**Implementation Tasks**:

### Phase 1: Create Local Utilities Directory

1. Create directory structure:
```bash
mkdir -p ~/repos/utilities/{error-logging,git-helpers,deployment,hooks}
cd ~/repos/utilities
```

2. Initialize git repo:
```bash
git init
git config user.email "claude@strongprompt.ai"
git config user.name "Claude Code"
```

3. Create version file and README:
```bash
echo "1.0.0" > VERSION
```

**README.md** (root):
```markdown
# Utilities Library

Shared scripts and utilities for Claude Code projects.

**Version**: 1.0.0
**Status**: Active

## Available Utilities

### Error Logging
- `error-logging/log-error.sh` (v1.0.0) - Logs Claude Code errors to PM error-queue
- Used by: lora_training, repo_docs, bfr-comms, strongprompt-website, mainstreak

### Git Helpers
- (Reserved for future helpers)

### Deployment
- (Reserved for future deployment scripts)

### Hooks
- (Reserved for Claude Code hook templates)

## Usage

**Development (local symlink)**:
```bash
ln -s ~/repos/utilities ~/.claude/utilities
echo 'source ~/.claude/utilities/error-logging/log-error.sh' >> .claude/hooks/post_tool_error.sh
```

**Production (git clone)**:
```bash
git clone https://github.com/StrongPromptAI/utilities.git .claude/utilities
```

## Adding New Utilities

Each utility gets its own subdirectory with:
- `utility-name.sh` (the script)
- `SPEC.md` (documentation, usage, dependencies, version)
- Tests (optional, in `tests/`)

## Versioning

Utilities follow semantic versioning. Update VERSION file when releasing.
```
```

### Phase 2: Move log-error.sh to Library

1. Move script:
```bash
cp ~/repos/pm/log-error.sh ~/repos/utilities/error-logging/log-error.sh
```

2. Create SPEC.md for log-error.sh:
```
~/repos/utilities/error-logging/SPEC.md
```

Content:
```markdown
# log-error.sh

**Version**: 1.0.0
**Purpose**: Log Claude Code tool errors to PM error-queue.json
**Location**: error-logging/log-error.sh

## Usage

```bash
~/repos/utilities/error-logging/log-error.sh "Tool" "error_type" "error_message" ["context"] ["skill_involved"] ["recovery"]
```

## Example

```bash
~/repos/utilities/error-logging/log-error.sh "Bash" "permission_denied" "Permission denied: /root" "lora_training: git push"
```

## Dependencies

- `jq` (JSON processor)
- `date` command (GNU or BSD)
- Write access to `~/repos/pm/error-queue.json`

## Error Types

- `not_found` - File/command not found
- `permission_denied` - Permission error
- `timeout` - Command timeout
- `other` - Miscellaneous

## Returns

Exits with 0 on success. Error ID printed to stdout.

## Changelog

### 1.0.0 (2026-01-10)
- Initial release
- Moved from ~/repos/pm/log-error.sh
```

### Phase 3: Update Project Hooks to Use Library

For each active project, update `.claude/hooks/post_tool_error.sh`:

1. **lora_training** (already using log-error.sh)
   - Update path from `~/repos/pm/log-error.sh` → `~/repos/utilities/error-logging/log-error.sh`

2. **repo_docs**
   - Same update

3. **bfr-comms**
   - Same update

4. **strongprompt-website**
   - Same update

5. **mainstreak**
   - Same update

Example updated hook:
```bash
#!/bin/bash
TOOL="${TOOL:-Unknown}"
ERROR_TYPE="${ERROR_TYPE:-other}"
ERROR_MESSAGE="${ERROR_MESSAGE:-Unknown error}"
~/repos/utilities/error-logging/log-error.sh "$TOOL" "$ERROR_TYPE" "$ERROR_MESSAGE" "[project-name]: tool error"
```

### Phase 4: Commit and Push to GitHub

1. Stage files:
```bash
cd ~/repos/utilities
git add .
```

2. Create initial commit:
```bash
git commit -m "Initial commit: Error logging utility library

- Move log-error.sh from pm/ to utilities/error-logging/
- Add SPEC.md documentation
- Create README with usage and structure
- Ready for project integration"
```

3. Create GitHub repo:
   - Go to https://github.com/new
   - Owner: StrongPromptAI
   - Name: utilities
   - Description: "Shared scripts and utilities for Claude Code projects"
   - Public
   - Create repository (do NOT initialize with README, already have one)

4. Add remote and push:
```bash
git remote add origin https://github.com/StrongPromptAI/utilities.git
git branch -M main
git push -u origin main
```

5. Create version tag:
```bash
git tag -a v1.0.0 -m "Release 1.0.0: Error logging utility"
git push origin v1.0.0
```

### Phase 5: Testing

1. Verify symlink works locally:
```bash
ln -s ~/repos/utilities ~/.claude/utilities
ls -la ~/.claude/utilities/error-logging/log-error.sh
```

2. Test from a project:
```bash
# Open Claude Code in lora_training
# Trigger an error (bad Bash command)
# Check error-queue.json appended correctly
cat ~/repos/pm/error-queue.json | jq '.[-1]'
```

3. Verify GitHub clone works:
```bash
git clone https://github.com/StrongPromptAI/utilities.git /tmp/test-utils
ls /tmp/test-utils/error-logging/
```

### Phase 6: Documentation

Create `utilities-setup.md` in repo_docs/skills/ or symlink_docs/ explaining:
- How developers add new utilities
- How projects reference utilities
- How to bump versions
- Example: Creating a new git-helpers/check-branch.sh utility

**Success Criteria**:
- [ ] ~/repos/utilities/ directory created with proper structure
- [ ] log-error.sh moved with SPEC.md documentation
- [ ] README.md explains library purpose and usage
- [ ] All 5 projects' hooks updated to use new library path
- [ ] GitHub repo created at StrongPromptAI/utilities
- [ ] Initial commit pushed with v1.0.0 tag
- [ ] Symlink and clone methods both tested working
- [ ] Error logging continues working from all projects
- [ ] Documentation created for future utilities

**Result**: Professional, version-controlled utilities library that scales from local development (symlinks) to production (git clone), following your existing symlink_docs + repo_docs/skills patterns.