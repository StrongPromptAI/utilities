# Utilities Library

**Project Phase**: PROD

Quick capture for tasks, dispatch to projects. Minimal execution - focus on planning and organization.

---

## KB Dashboard

The KB Dashboard is a read-only React + FastAPI app that surfaces decisions, tasks, calls, search, clusters, and clients from the `knowledge_base` PostgreSQL database.

**URL**: http://localhost:5176

### Starting the dashboard

When the user says "show tasks", "dashboard", or wants to view tasks/decisions/calls:

Port assignments: see `~/repo_docs/PORT_REGISTRY.md` (backend 8006, frontend 3006)

1. Check if both servers are running:
   - API: `curl -s http://localhost:8006/health` (expect `{"service":"kb-dashboard"}`)
   - Frontend: `curl -s -o /dev/null -w "%{http_code}" http://localhost:3006/` (expect `200`)
2. If API is down, start it:
   ```bash
   cd ~/repos/utilities && uv run uvicorn dashboard.api.main:app --port 8006 --reload &
   ```
3. If frontend is down, tell the user to run in a separate terminal:
   ```
   cd ~/repos/utilities/dashboard/frontend && npm run dev
   ```
4. Provide the URL: http://localhost:3006
5. Tell user to select the "development" project to see their task list

### Adding a task

Insert directly into the database:

```sql
INSERT INTO action_items (project_id, title, description, assigned_to, status, prompt_file, created_at)
VALUES (7, '#N: <title>', '<description>', '<Chris Martin|Quinlan Anderson>', 'open', NULL, now());
```

- `project_id=7` is the "development" project
- For tasks with prompts, store the prompt at `~/repo_docs/utilities/plans/task-{id}-{slug}.md` and set `prompt_file` to the filename

### Picking a task

When the user picks a task:

1. Generate a **Claude Code prompt** they can paste into the target project
2. Store the prompt in `~/repo_docs/utilities/plans/task-{id}-{slug}.md`
3. The prompt should include what to do, relevant context/skills, and success criteria
4. Update the action_items row: set `prompt_file` and status to `open`

### Completing tasks

```sql
UPDATE action_items SET status = 'done', completed_at = now() WHERE id = <id>;
```

### Rules

- **No execution here** - only capture, plan, and generate prompts
- Keep it fast - one task in, one prompt out
- Focus on task planning, not project-specific execution
- **After every chat completion**: Suggest opening the dashboard to keep task visibility

---

## Railway

**API only — never use the Railway CLI.** The CLI session cookie (`rw_Fe26.*`) expires constantly and `railway login` requires interactive auth. Use the GraphQL API with the account-level API token.

**Token location**: `~/.railway/config.json` → `user.apiToken` (UUID format)

```bash
# Read token
TOKEN=$(python3 -c "import json; print(json.load(open('$HOME/.railway/config.json'))['user']['apiToken'])")

# Test auth
curl -s -X POST https://backboard.railway.com/graphql/v2 \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"query { me { email name } }"}'
```

**Project IDs (production)**:
- Project: `a3677be5-5392-473e-b609-f23b7c06b78c`
- Environment: `3317309b-8f0c-43f4-9d8a-73b1c9fecf9c`
- hj-roadmap service: `5d97ef67-1434-487a-9069-df8b98a0dd95`
- Postgres service: `ae33aa6f-3890-4af7-aec6-13904be1c242`
- Workspace: `ddd86c61-bd3b-4316-9f5c-d44541c66cc3`

**Domain**: `roadmap.pop.clinic`

**Pull API vars**:
```bash
TOKEN=$(python3 -c "import json; print(json.load(open('$HOME/.railway/config.json'))['user']['apiToken'])")
curl -s -X POST https://backboard.railway.com/graphql/v2 \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"query":"query { variables(projectId: \"a3677be5-5392-473e-b609-f23b7c06b78c\", environmentId: \"3317309b-8f0c-43f4-9d8a-73b1c9fecf9c\", serviceId: \"5d97ef67-1434-487a-9069-df8b98a0dd95\") }"}'
```

**If token is missing**: User must create a new one at railway.com/account/tokens (account-level, NOT workspace-scoped) and write it to `~/.railway/config.json` → `user.apiToken`.

---

## Shared Services (embed + STT + TTS + Whisper)

Shared services live under `services/` and are deployed in the `shared-svcs` Railway project. Four services today:

- `services/embed/` — nomic-embed-text-v1.5 ONNX, 768 dims, port 8100. Two endpoints: TEI-native `/embed` and OpenAI-compatible `/v1/embeddings`.
- `services/stt/` — sherpa-onnx streaming speech-to-text. Port 8101. WebSocket `/transcribe`.
- `services/tts/` — Kokoro-82M ONNX text-to-speech, 24 kHz mono. Port 8102. OpenAI-compatible REST `/v1/audio/speech`. Voice allowlist (default `af_heart`); env-tunable via `TTS_VOICE_ALLOWLIST`.
- `services/whisper/` — faster-whisper batch transcription. OpenAI-compatible REST `/v1/audio/transcriptions`.

### Environment-gated auth (dev = off, prod/staging = on)

Both services check `ENVIRONMENT` (explicit) or `RAILWAY_ENVIRONMENT` (auto-set by Railway on every service) at startup. Auth enforcement is keyed off that:

| Where | `ENVIRONMENT` | `RAILWAY_ENVIRONMENT` | Mode | Auth |
|-------|--------------|----------------------|------|------|
| Fresh local clone | unset | unset | development | **off** |
| Local prod-auth test | `production` | unset | production | on |
| Railway production | unset | `production` (auto) | production | on |
| Railway staging (future) | unset | `staging` (auto) | staging | on |

Default is committed in code (`app.py`). No per-machine `.env` file, no shell profile setup, no gitignored config to sync. A fresh clone on any new machine runs with auth off; Railway deploys run with auth on automatically via its own `RAILWAY_ENVIRONMENT` auto-population.

### Starting locally

```bash
cd ~/repos/utilities/services/embed && uv run uvicorn app:app --port 8100
cd ~/repos/utilities/services/stt   && uv run uvicorn app:app --port 8101
cd ~/repos/utilities/services/tts   && uv run uvicorn app:app --port 8102
```

No env vars needed. No token minting. All endpoints serve unauthenticated.

### Endpoints — embed

- `GET /health` — public, always — returns `{"status":"ok","model":"embed","dims":768}`
- `GET /mem` — public, always — RSS for OOM monitoring
- `POST /embed` — auth enforced only in prod/staging — TEI shape: `{"inputs": [...]}` → `[[...]]`
- `POST /v1/embeddings` — auth enforced only in prod/staging — OpenAI shape: `{"input": [...], "model": "..."}` → `{"data": [{"embedding": [...]}], ...}`

### Endpoints — STT

- `GET /health` — public, always
- `WS /transcribe` — in prod, first WS text frame must be a valid JWT (`aud="stt"`); in dev, the first-frame JWT check is skipped entirely (any text frame is accepted and the WS connects immediately)

### Endpoints — TTS

- `GET /health` — public, always — returns `{"status":"ok","model":"kokoro-82m-v1.0","voices_allowed":[...],"sample_rate":24000,"license":"Apache-2.0"}`
- `POST /v1/audio/speech` — OpenAI-compatible JSON body: `{"input": "...", "voice": "af_heart", "response_format": "wav"|"pcm", "speed": 1.0, "language": "en-us"}`. Returns `audio/wav` or `audio/L16; rate=24000; channels=1`. Auth `aud="tts"` enforced in prod/staging only. Per-request limits: max 800 chars input, voice must be in allowlist, speed 0.5–2.0.

### Minting tokens (prod consumers)

`services/shared_auth/token.py` exposes `make_embed_token(ttl_seconds=1800)`, `make_stt_token(ttl_seconds=300)`, and `make_tts_token(ttl_seconds=300)`. Reads `SHARED_SVC_JWT_SECRET` (must equal the service's `JWT_SECRET`) and `SERVICE_NAME`.

In-process backend consumers (e.g. iTheraputix): `from shared_auth.token import make_embed_token` and call inline, refreshing before expiry.

Command-line / out-of-process consumers that need a token for prod-mirror testing:
```bash
TOKEN=$(SHARED_SVC_JWT_SECRET=$JWT_SECRET SERVICE_NAME=local-dev \
  uv run python -c "import sys; sys.path.insert(0, '..'); \
  from shared_auth.token import make_embed_token; print(make_embed_token(ttl_seconds=86400))")
```

### gitnexus integration (local development)

gitnexus speaks OpenAI-compatible HTTP embeddings. With the local embed service running unauthenticated in dev:

```bash
export GITNEXUS_EMBEDDING_URL=http://localhost:8100/v1
export GITNEXUS_EMBEDDING_MODEL=nomic-embed-text-v1.5
export GITNEXUS_EMBEDDING_DIMS=768
# GITNEXUS_EMBEDDING_API_KEY unset — gitnexus sends "Bearer unused", dev mode ignores
cd ~/repos/<any-project> && gitnexus analyze --embeddings
```

See `~/repo_docs/skills/gitnexus/SKILL.md` for the gitnexus-side env-var contract (required vars, wire protocol, fallback behavior).

### Skill Radar hook

`utilities/scripts/skill_hook/hook.py` calls `http://localhost:8100/embed` without an Authorization header — relies on dev-mode auth-off to land. This is why local dev must stay auth-off by default, not require per-machine token setup.

### Railway env

Production on `shared-svcs` project: `JWT_SECRET` set (64 chars) on all four services (embed, stt, whisper, tts). `RAILWAY_ENVIRONMENT=production` auto-populated. Explicit `ENVIRONMENT` not needed on Railway — the fallback to `RAILWAY_ENVIRONMENT` handles it. If setting up a new Railway environment (e.g. staging), Railway's auto `RAILWAY_ENVIRONMENT` value will be that environment's name, which must be one of `production` / `staging` for auth to turn on.

### Railway IDs & URLs

**Project**: `504e0aec-fb69-443b-9786-139b5fe50e0a`
**Environment (production)**: `1ea8ab63-10af-4b83-b562-68a4a5c4f670`
**Workspace**: `ddd86c61-bd3b-4316-9f5c-d44541c66cc3`

| Service | ID | URL |
|---------|-----|-----|
| stt | `d86f18dc-b843-41e2-b67a-c8ffbeca3817` | `wss://shared-svcs-stt.up.railway.app/transcribe` |
| embed | `ab604f00-e72c-4865-b362-843f585e2051` | `https://shared-svcs-embed.up.railway.app/embed` |
| whisper | `2fe8a99d-8e57-47de-b7e5-f7ef4371cf66` | `https://shared-svcs-whisper.up.railway.app/v1/audio/transcriptions` |
| tts | `02ff6d94-a49c-464e-b1e0-44f6933d5209` | `https://shared-svcs-tts.up.railway.app/v1/audio/speech` |

Whisper reuses `aud="stt"` — one token type works for both streaming STT (WS) and Whisper batch (REST). TTS uses its own `aud="tts"`.

**GraphQL config**: `rootDirectory` without leading slash, `dockerfilePath: Dockerfile`, `watchPatterns: ["**"]`.

---

## Index Freshness

GitNexus call-graph + cluster skills are kept current by a post-commit hook. Run once per fresh clone:

```bash
bash scripts/install-git-hooks.sh
```

This installs `.git/hooks/post-commit`, which runs `gitnexus analyze --skills --skip-agents-md` in the background after every commit. Generated cluster skills land in `.claude/skills/generated/` (gitignored). The pre-commit hook reads the `RESULT:` marker from the regen log and aborts on regen failure.

Manual fallback if the hook is uninstalled: `gitnexus analyze --skills --skip-agents-md`.

---

## oxp-kb (OpenWebUI on Railway)

Self-hosted OpenWebUI stack on Railway project `oxp-kb`. Public surface: `oxp.chat`. Full stack guide: `~/repo_docs/utilities/guides/26-4-17_GUIDE_oxp-kb-stack.md`.

### Railway IDs

**Project**: `96a6d9dd-b680-4821-bee6-ed850a19074b`
**Environment (production)**: `30bf77ef-ec92-472d-b92a-93e3806bd7e4`
**Workspace**: `ddd86c61-bd3b-4316-9f5c-d44541c66cc3`

| Service | ID |
|---------|-----|
| openwebui | `d373f3ec-d7b9-4939-b399-37a8cdcebf1d` |
| caddy | `6a3e8be7-db29-42a3-aded-b86c3029941a` |
| postgres | `6e3ccd17-d0fd-42c5-8edd-bef785445c57` |
| oxp-files-app | `56aebab1-320e-48d2-9053-44cacc82c241` — FastAPI files app at `services/files/`, replaces SFTPGo (deleted 2026-05-06). Single shared folder backed by Tigris bucket `modular-packet-cuxv1ti4ca` prefix `shared/`. Browser uploads/downloads via presigned PUT/GET URLs (body bypasses Caddy). |
| oidc-otp | `bbefc796-5871-428c-ab82-4b44e29d112b` — IdP. Sets `oxp_sso` cookie on its host for cross-service silent re-auth across oxp.chat + oxp.files (30-day TTL). |
| tika | `38bc46e6-1a8f-4c72-b6e8-f34e62faab45` |

### Document extraction (Tika)

PDF / Office uploads route through **Apache Tika**, deployed as a sidecar service. The default OpenWebUI extractor (pypdf) returns empty content for image-based and many real-world PDFs, which surfaces in the UI as a vague "type error" and in logs as `ValueError: The content provided is empty` from `open_webui.routers.retrieval:process_file`.

Wired via:
- `CONTENT_EXTRACTION_ENGINE=tika` on openwebui
- `TIKA_SERVER_URL=http://tika.railway.internal:9998` on openwebui
- `tika` service is `apache/tika:latest`, listens on `0.0.0.0:9998`

If uploads still produce empty content after Tika is in place, the PDF is fully image-only / scanned with no text layer — Tika does not OCR by default. Add Docling or pre-OCR before upload.

### Gotcha — `PersistentConfig` env vars

OpenWebUI marks many of its env vars (`CONTENT_EXTRACTION_ENGINE`, `TIKA_SERVER_URL`, all `RAG_*`, `PDF_*`, etc.) as **`PersistentConfig`**. The env-var value is read **only on first boot** into the runtime database; after that, the DB row wins and env-var changes are silently ignored.

When changing one of these later:
1. Update the env var in Railway (for fresh containers / future re-init), AND
2. Update the same setting in **Admin Settings → Documents** (or the relevant tab) in the OpenWebUI UI, OR
3. Clear the persisted config row in postgres before redeploying.

If you only do (1), the redeploy looks healthy but behavior is unchanged. The OpenWebUI docs flag which vars are `PersistentConfig` next to each variable's definition.

Stakeholder intelligence system. User asks natural language questions, you run `kb` commands via Bash tool.

Pipeline (plan 26-5-21): ingest CSV → outline → summarize → review.

**Common requests:**
- "Search kb for [topic]"
- "Show me all stakeholders"
- "What calls did I have with [name]?"
- "Tell me about [stakeholder name]"
- "Summarize call [id] using my outline" (or "draft an outline for call [id], then summarize")
- "Scrub PHI from [file]" (standalone Presidio utility)

**Core commands** (full list: `kb --help`):
- `kb ingest <csv>` — load a Dialpad CSV
- `kb outline <call_id> [--content FILE | --edit]` — write/edit the bullet outline that drives the summary
- `kb summarize <call_id> [--phi]` — generate outline-aligned markdown summary via primary LLM (Opus 4.7) with backup (Gemini 3.5 Flash) on transient errors
- `kb show-summary <call_id>` — print stored summary
- `kb scrub <input> -o <output> [--mapping <json>]` — standalone Presidio scrub, works on any text file

**LLM routing**: `kb_config.primary_llm_*` and `backup_llm_*` columns pin the providers. Defaults: `anthropic/claude-opus-4.7` primary + `google/gemini-3.5-flash` backup, both via OpenRouter. Switch by updating the row.

**PHI**: per-call opt-in via `--phi`. Default off (most meetings don't need it). Token map is per-call, deterministic, stable across all chunks.

**When user asks about kb:**
1. Read `symlink_docs/plans/kb-guide.md` for full command reference
2. Run appropriate `uv run python scripts/kb` commands
3. Present results in conversational format

**Guide location:** `symlink_docs/plans/kb-guide.md`
