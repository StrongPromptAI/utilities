"""Status dashboard — serves static PWA + parses journalctl smoke results."""

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI()

SMOKE_UNIT = "devops-smoke.service"
SMOKE_INTERVAL_MIN = 10


def parse_latest_smoke_result() -> dict | None:
    """Run journalctl and extract the most recent SmokeResult JSON."""
    try:
        proc = subprocess.run(
            [
                "journalctl", "-u", SMOKE_UNIT,
                "--no-pager", "-n", "50", "--output=cat",
            ],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None

    # Walk lines in reverse to find the latest SmokeResult
    for line in reversed(proc.stdout.strip().splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "test_results" in obj:
            return obj
    return None


@app.get("/api/status")
def get_status():
    result = parse_latest_smoke_result()
    if result is None:
        return JSONResponse(
            {"error": "no smoke results found", "project": "unknown", "tests": []},
            status_code=503,
        )

    last_run = result.get("timestamp")
    next_run = None
    if last_run:
        try:
            dt = datetime.fromisoformat(last_run)
            next_run = (dt + timedelta(minutes=SMOKE_INTERVAL_MIN)).isoformat()
        except ValueError:
            pass

    tests = [
        {
            "name": t["name"],
            "status": "pass" if t["passed"] else "fail",
            "latency_ms": round(t.get("latency_ms") or 0),
        }
        for t in result.get("test_results", [])
    ]

    return {
        "project": result.get("message", "").split(":")[0] if result.get("message") else "unknown",
        "ok": result.get("ok", False),
        "tests": tests,
        "tests_passed": result.get("tests_passed", 0),
        "tests_total": result.get("tests_total", 0),
        "last_run": last_run,
        "next_run": next_run,
    }


@app.get("/health")
def health():
    return {"service": "status-dashboard"}


# Static files last — catch-all
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
