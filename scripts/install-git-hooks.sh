#!/usr/bin/env bash
# Install git hooks from scripts/git-hooks/ into .git/hooks/
#
# Idempotent — safe to re-run. Uses symlinks so future hook edits in
# scripts/git-hooks/ propagate without re-installing. Runs once per fresh
# clone; documented in project CLAUDE.md § Index freshness.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
HOOK_DIR="$REPO_ROOT/.git/hooks"
SOURCE_DIR="$REPO_ROOT/scripts/git-hooks"

if [ ! -d "$SOURCE_DIR" ]; then
    echo "error: $SOURCE_DIR not found"
    exit 1
fi

mkdir -p "$HOOK_DIR"

installed=0
for src in "$SOURCE_DIR"/*; do
    [ -f "$src" ] || continue
    name="$(basename "$src")"
    target="$HOOK_DIR/$name"

    # Replace existing hook (could be from git init, or an old symlink)
    rm -f "$target"
    ln -s "$src" "$target"
    chmod +x "$src"
    echo "Installed: $name → $target"
    installed=$((installed + 1))
done

echo
if [ "$installed" -eq 0 ]; then
    echo "No hooks to install — $SOURCE_DIR is empty"
    exit 0
fi
echo "$installed hook(s) installed. Verify:"
echo "  ls -la $HOOK_DIR/ | grep -v sample"
