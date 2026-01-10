# Error Logging Utility

**Version**: 1.0.0
**Status**: Active
**Last Updated**: 2026-01-10T15:40:00-07:00

---

## Purpose

Centralized error capture and logging for Claude Code tool failures. Used by Claude Code `.claude/hooks/post_tool_error.sh` to record errors to a JSON queue for analysis and debugging.

---

## Features

- ✅ Auto-incrementing error IDs
- ✅ ISO 8601 timestamps
- ✅ Categorized error types
- ✅ Structured JSON output
- ✅ Graceful creation of error-queue.json if missing
- ✅ Atomic writes (uses temp file + mv)

---

## Usage

### Basic

```bash
~/repos/utilities/error-logging/log-error.sh "Tool" "error_type" "error_message" "context"
```

### Full Syntax

```bash
log-error.sh <tool> <error_type> <error_message> [context] [skill_involved] [recovery]
```

### Arguments

| Position | Argument | Required | Example |
|----------|----------|----------|---------|
| 1 | `tool` | Yes | `Bash`, `Edit`, `Read`, `Grep` |
| 2 | `error_type` | Yes | `not_found`, `syntax`, `permission`, `timeout`, `other` |
| 3 | `error_message` | Yes | "File not found: /path/to/file" |
| 4 | `context` | No | "lora_training: schema migration" |
| 5 | `skill_involved` | No | "postgres" |
| 6 | `recovery` | No | "Check file path, then retry" |

---

## Error Types

Standard error categories:

```
not_found       - File, path, or resource not found
permission      - Permission denied, access denied
timeout         - Operation timed out
syntax          - Syntax error, parse error, invalid input
git_conflict    - Git conflict, push rejection
other           - Uncategorized error
```

---

## Examples

### Example 1: File Not Found (from Claude Code Bash)

```bash
~/repos/utilities/error-logging/log-error.sh \
  "Bash" \
  "not_found" \
  "No such file or directory: /tmp/missing.txt" \
  "lora_training: schema migration"
```

**Output**:
```
✓ Error #42 logged: Bash - not_found
```

**Queue entry**:
```json
{
  "id": 42,
  "timestamp": "2026-01-10T15:40:33-07:00",
  "tool": "Bash",
  "error_type": "not_found",
  "error_message": "No such file or directory: /tmp/missing.txt",
  "context": "lora_training: schema migration",
  "skill_involved": "",
  "recovery": "",
  "status": "open"
}
```

### Example 2: Syntax Error with Recovery Hint

```bash
~/repos/utilities/error-logging/log-error.sh \
  "Edit" \
  "syntax" \
  "Invalid regex pattern in old_string" \
  "repo_docs: planning skill update" \
  "edit" \
  "Escape special characters or simplify pattern"
```

### Example 3: From Project Hook

```bash
# In .claude/hooks/post_tool_error.sh
LOG_ERROR_SCRIPT="$HOME/repos/utilities/error-logging/log-error.sh"

if [ -f "$LOG_ERROR_SCRIPT" ]; then
  "$LOG_ERROR_SCRIPT" "$TOOL" "$ERROR_TYPE" "$ERROR_MESSAGE" "$CONTEXT" "$SKILL" "$RECOVERY"
fi
```

---

## Dependencies

| Dependency | Purpose | Install |
|------------|---------|---------|
| `bash` | Script shell | Built-in |
| `jq` | JSON manipulation | `brew install jq` |
| `date` | Timestamp generation | Built-in |

### Check Dependencies

```bash
command -v jq &> /dev/null && echo "✅ jq installed" || echo "❌ Install jq: brew install jq"
```

---

## Output Location

**Default**: `~/.error-queue.json` (user's home directory)

**Format**: JSON array of error objects

```bash
# View all logged errors
cat ~/.error-queue.json | jq '.'

# View last error
cat ~/.error-queue.json | jq '.[-1]'

# Count errors by type
cat ~/.error-queue.json | jq 'group_by(.error_type) | map({type: .[0].error_type, count: length})'

# View errors from specific tool
cat ~/.error-queue.json | jq '.[] | select(.tool == "Bash")'
```

---

## Error Queue JSON Structure

```json
[
  {
    "id": 1,
    "timestamp": "2026-01-10T15:40:33-07:00",
    "tool": "Bash",
    "error_type": "syntax",
    "error_message": "Parse error on line 42",
    "context": "lora_training: data migration",
    "skill_involved": "postgres",
    "recovery": "Fix the regex pattern",
    "status": "open"
  },
  {
    "id": 2,
    "timestamp": "2026-01-10T15:41:12-07:00",
    "tool": "Edit",
    "error_type": "not_found",
    "error_message": "String to replace not found",
    "context": "repo_docs: planning docs",
    "skill_involved": "",
    "recovery": "",
    "status": "open"
  }
]
```

---

## Exit Codes

| Exit Code | Meaning | When |
|-----------|---------|------|
| 0 | Success | Error logged successfully |
| 1 | Error | jq failed, file write failed, or other fatal error |

---

## Atomic Writes

The script uses atomic writes to prevent corruption:

```bash
# Write to temporary file
jq ". += [$NEW_ERROR]" "$QUEUE_FILE" > "$QUEUE_FILE.tmp"

# Atomic rename (prevents partial writes)
mv "$QUEUE_FILE.tmp" "$QUEUE_FILE"
```

This ensures error-queue.json is never left in a corrupted state.

---

## Workflow: From Claude Code Hook to Queue

```
1. Claude Code tool fails (e.g., Bash exits with non-zero)
2. post_tool_error.sh hook fires
3. Hook extracts error details: tool name, type, message
4. Hook calls: log-error.sh "Bash" "syntax" "Parse error..." "context"
5. log-error.sh creates JSON entry with auto-incrementing ID
6. Entry appended to ~/.error-queue.json
7. Developer can analyze queue: jq '.[] | select(.error_type == "syntax")'
```

---

## Common Patterns

### Use from Project Hooks

```bash
LOG_ERROR_SCRIPT="$HOME/repos/utilities/error-logging/log-error.sh"

if [ ! -f "$LOG_ERROR_SCRIPT" ]; then
  echo "⚠️  Utilities repo required. Clone: git clone https://github.com/StrongPromptAI/utilities.git ~/repos/utilities"
  exit 0
fi

# Call with error details
"$LOG_ERROR_SCRIPT" "$TOOL" "$ERROR_TYPE" "$ERROR_MESSAGE" "$CONTEXT" "" ""
```

### Analyze Errors

```bash
# Find all unresolved errors in last 24h
cat ~/.error-queue.json | jq '.[] | select(.status == "open" and (now - (.timestamp | fromdate)) < 86400)'

# Count by error type
cat ~/.error-queue.json | jq 'group_by(.error_type) | map({type: .[0].error_type, count: length}) | sort_by(.count) | reverse'

# Find errors during specific project work
cat ~/.error-queue.json | jq '.[] | select(.context | contains("lora_training"))'
```

---

## Troubleshooting

### "jq: command not found"

**Cause**: jq not installed
**Fix**: `brew install jq`

### Error queue not growing

**Cause**: Hook script path is wrong, or utilities repo not cloned
**Fix**: Verify `~/repos/utilities/error-logging/log-error.sh` exists: `ls -la ~/repos/utilities/error-logging/log-error.sh`

### "Permission denied" on error-queue.json

**Cause**: File ownership issue
**Fix**: `chmod 644 ~/.error-queue.json && rm ~/.error-queue.json.tmp 2>/dev/null || true`

### Invalid JSON in error-queue.json

**Cause**: Partial write or corruption
**Fix**: Restore from backup or manually validate: `jq -e . ~/.error-queue.json`

---

## Changelog

### v1.0.0 (2026-01-10)
- ✅ Initial release
- ✅ Auto-incrementing IDs
- ✅ ISO 8601 timestamps (UTC-7)
- ✅ Atomic writes to prevent corruption
- ✅ Graceful creation of error-queue.json
- ✅ Support for 6 error categories

---

## Future Enhancements

Possible improvements (not in v1.0.0):

- [ ] Rotate error-queue.json weekly (prevent unbounded growth)
- [ ] Add error severity levels (critical, warning, info)
- [ ] Add skill-specific recovery hints
- [ ] Export to error-analysis dashboard
- [ ] Webhook integration for Slack/Discord alerts

---

## Integration Checklist

When integrating `log-error.sh` into a project hook:

- [ ] Reference: `~/repos/utilities/error-logging/log-error.sh`
- [ ] Verify utilities repo is cloned locally
- [ ] Check dependencies: `jq` installed
- [ ] Test: Trigger test error, verify queue entry created
- [ ] Verify output: `cat ~/.error-queue.json | jq '.[-1]'`
- [ ] Document in project README

---

*Error Logging Utility v1.0.0 - 2026-01-10T15:40:00-07:00*
