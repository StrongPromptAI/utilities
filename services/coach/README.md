# coach service

Phase 0 (graphics slice) of the dedicated sales-coach service — plan
`symlink_docs/plans/26-6-20_dedicated-sales-coach-service.md`. Serves the
book/manual figures that the KB `reference_docs` corpus cites, off a Railway
volume in the **kb project** (internal routing to Postgres; minimal egress).

The chat backend, retrieval, widget, and the `coach.strongprompt.ai` CNAME are
grown onto this same service later (plan 26-6-20 Phases 1+).

## Routes
- `GET /health` → `{"status":"ok"}`
- `GET /figures/{name}` → the figure (FileResponse) from the volume
- `POST /figures` → upload a figure (multipart `file`); **HS256 bearer**, `aud=coach-upload`, secret `COACH_UPLOAD_SECRET`

## Env
- `COACH_FIGURES_ROOT` — volume mount (default `/data/figures`)
- `COACH_UPLOAD_SECRET` — HS256 secret for the upload token (headless ingest mints it)
- `PORT` — Railway-injected

## Run local
```bash
COACH_FIGURES_ROOT=./data/figures COACH_UPLOAD_SECRET=dev-secret \
  uv run uvicorn app:app --port 8104
```

## Deploy
New Railway service in the **kb project** (`a3677be5`, prod env `3317309b`), Docker, volume at `/data/figures`, env `COACH_UPLOAD_SECRET`. Railway default URL (no CNAME for graphics; the `coach.strongprompt.ai` CNAME lands with the chat in plan 26-6-20).
