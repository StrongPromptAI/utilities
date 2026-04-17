"""
Claude Code PostToolUse hook — semantic skill suggestion on errors.

Fired by Claude Code after every Bash tool use. When an error is detected,
embeds the error text and finds the closest matching skill chunk in the index.

Outcomes:
- A: Match found (score >= threshold) AND skill is genuinely relevant —
     guidance injected into context. Also logged to SKILL_INJECT_LOG.md.
- B: No match (all scores below threshold) — logged to SKILL_DEBT.md for
     periodic review and skill-coverage improvement.
- C: Match found (score >= threshold) BUT skill is a false positive —
     guidance injected (may be noise), ALSO logged to SKILL_INJECT_LOG.md.
     Reviewing the inject log periodically is how Outcome C is detected.

Exits silently (code 0) on any failure — never blocks Claude Code.
"""

import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

INDEX_PATH = Path.home() / ".claude/skill_index.json"
SKILL_DEBT_PATH = Path.home() / "repo_docs/skills/SKILL_DEBT.md"
SKILL_INJECT_LOG_PATH = Path.home() / "repo_docs/skills/SKILL_INJECT_LOG.md"
EMBED_URL = "http://localhost:8100/embed"
QUERY_PREFIX = "search_query: "
THRESHOLD = 0.70
TOP_N = 2
CONTEXT_CHARS = 800  # max chars of chunk text to inject per match
DEBT_SNIPPET_CHARS = 400  # error snippet saved to skill debt log
INJECT_SNIPPET_CHARS = 200  # error snippet saved to inject log

ERROR_SIGNALS = re.compile(
    r"(traceback|error:|exception:|exit code [1-9]|no such file|command not found"
    r"|permission denied|modulenotfounderror|importerror|syntaxerror|typeerror"
    r"|attributeerror|keyerror|valueerror|connectionrefused|timeout|failed to|"
    r"cannot|could not|not found)",
    re.IGNORECASE,
)


def dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def embed(text: str) -> list[float] | None:
    try:
        payload = json.dumps({"inputs": [text]}).encode()
        req = urllib.request.Request(
            EMBED_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            return json.loads(resp.read())[0]
    except Exception:
        return None


def extract_error(output: str) -> str | None:
    if not ERROR_SIGNALS.search(output):
        return None
    snippet = output.strip()[-600:]
    snippet = re.sub(r"\x1b\[[0-9;]*m", "", snippet)
    return snippet.strip()


def load_index() -> list[dict] | None:
    if not INDEX_PATH.exists():
        return None
    try:
        return json.loads(INDEX_PATH.read_text())
    except Exception:
        return None


def log_skill_debt(error_text: str, top_scored: list[dict]) -> None:
    """Prepend a miss entry to SKILL_DEBT.md."""
    try:
        if not SKILL_DEBT_PATH.exists():
            return

        ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
        snippet = error_text[:DEBT_SNIPPET_CHARS].replace("```", "~~~")

        best_lines = []
        for s in top_scored[:3]:
            skill = s.get("skill_name", s.get("name", "?"))
            header = s.get("header", "")
            best_lines.append(f"  {s['score']:.2f}  {skill} › {header}")

        best_block = "\n".join(best_lines) if best_lines else "  (index empty or embed unavailable)"

        entry = (
            f"\n## {ts}\n\n"
            f"**Best scores (all below {THRESHOLD}):**\n"
            f"{best_block}\n\n"
            f"**Error snippet:**\n"
            f"```\n{snippet}\n```\n\n"
            f"---\n"
        )

        content = SKILL_DEBT_PATH.read_text()
        # Insert after the closing comment marker
        marker = "<!-- New entries are prepended by hook.py — most recent at top -->"
        if marker in content:
            content = content.replace(marker, marker + entry, 1)
        else:
            content = content + entry

        SKILL_DEBT_PATH.write_text(content)
    except Exception:
        pass  # never block Claude Code


def log_skill_inject(error_text: str, matches: list[dict]) -> None:
    """Append an inject entry to SKILL_INJECT_LOG.md.

    Covers Outcome C: matches above threshold that may be false positives.
    Reviewing this log periodically surfaces cases where the radar injected
    irrelevant skill content — the signal for a precision gap.
    """
    try:
        ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
        snippet = error_text[:INJECT_SNIPPET_CHARS].replace("```", "~~~")

        match_lines = []
        for m in matches:
            skill = m.get("skill_name", m.get("name", "?"))
            header = m.get("header", "")
            match_lines.append(f"  {m['score']:.2f}  {skill} › {header}")

        entry = (
            f"\n## {ts}\n\n"
            f"**Injected ({len(matches)} match{'es' if len(matches) != 1 else ''}):**\n"
            f"{chr(10).join(match_lines)}\n\n"
            f"**Error snippet:**\n"
            f"```\n{snippet}\n```\n\n"
            f"---\n"
        )

        marker = "<!-- Entries appended by hook.py — most recent at top -->"

        if not SKILL_INJECT_LOG_PATH.exists():
            header_block = (
                "---\n"
                "description: Errors where the Skill Radar injected skill content "
                "(score >= threshold). Review periodically to catch false positives "
                "(Outcome C) — cases where the injected skill was irrelevant.\n"
                "workflow: |\n"
                "  1. Scan entries — does the injected skill match the error domain?\n"
                "  2. True positives: leave as-is or mark ## OK\n"
                "  3. False positives: add a note, then improve the relevant skill\n"
                "     chunk so future embeddings score lower for unrelated errors.\n"
                "  4. Trim old entries when the file gets long (keep last ~30).\n"
                "---\n\n"
                "# Skill Inject Log\n\n"
                f"{marker}\n"
            )
            SKILL_INJECT_LOG_PATH.write_text(header_block)

        content = SKILL_INJECT_LOG_PATH.read_text()
        if marker in content:
            content = content.replace(marker, marker + entry, 1)
        else:
            content = content + entry

        SKILL_INJECT_LOG_PATH.write_text(content)
    except Exception:
        pass  # never block Claude Code


def main():
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw)
    except Exception:
        sys.exit(0)

    if payload.get("tool_name") != "Bash":
        sys.exit(0)

    resp = payload.get("tool_response", "")
    output = resp if isinstance(resp, str) else json.dumps(resp)

    error_text = extract_error(output)
    if not error_text:
        sys.exit(0)

    index = load_index()
    if not index:
        sys.exit(0)

    query_vec = embed(QUERY_PREFIX + error_text)
    if not query_vec:
        sys.exit(0)

    scored = sorted(
        [{"score": dot(query_vec, s["embedding"]), **s} for s in index],
        key=lambda x: x["score"],
        reverse=True,
    )

    matches = [s for s in scored[:TOP_N] if s["score"] >= THRESHOLD]

    if not matches:
        log_skill_debt(error_text, scored[:3])
        sys.exit(0)

    # Log every injection — Outcome C (false positives above threshold) is
    # only detectable by reviewing this log, not from SKILL_DEBT.md.
    log_skill_inject(error_text, matches)

    lines = ["Skill Radar — relevant section(s) for this error:"]
    lines.append("")
    for m in matches:
        skill = m.get("skill_name", m.get("name", "?"))
        header = m.get("header", "")
        fpath = m.get("file_path", "")
        score = m["score"]
        text = m.get("text", m.get("description", ""))

        lines.append(f"[{score:.2f}] {skill} › {header}  ({fpath})")
        lines.append("---")
        lines.append(text[:CONTEXT_CHARS])
        lines.append("")

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": "\n".join(lines),
        }
    }))


if __name__ == "__main__":
    main()
