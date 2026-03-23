# Utilities — KB & Product Intelligence

> **Quick reference for all work.** For system architecture, see `symlink_docs/guides/26-3-7_GUIDE_kb-system-reference.md`. For KB commands, see `symlink_docs/plans/26-1-26-kb-guide.md`.

**Project Phase**: PROD

**Updated:** 2026-03-07T08:00:00-07:00

---

## Routing Activation (First Step Every Task)

**Before exploring, planning, or writing code:**

1. Scan the **Task Routing** table below
2. Load **all guides** from matching rows
3. Activate **all skills** from the "Global Skill" column
4. Then plan or code

---

## System Architecture

Stakeholder intelligence platform: ingest call transcripts, chunk with embeddings, harvest decisions/questions/actions, synthesize per-stakeholder docs, drive product roadmaps.

```
KB CLI (kb_cli.py)  →  kb_core library  →  PostgreSQL 18 + pgvector (knowledge_base)
KB Ingest           →  (scripts/kb_core/)→  LM Studio :1234 (local GPU inference)
Dashboard API       →  CRUD layer       →  ONNX embeddings (nomic-embed-text 768d)
Dashboard Frontend  →  Tailwind v4      →  Semantic theme tokens (data-theme switching)
Caddy (local)       →  kb.localhost      →  /api/* → :8006, /* → :3006
```

**Ports**: Per `~/repo_docs/PORT_REGISTRY.md` (utilities = project #6: backend 8006, frontend 3006)

**Cross-project access** (symlinked from repo_docs):
- `symlink_docs/stakeholders/itheraputix/` — stakeholder intelligence docs (doctor.md, patient.md, etc.)
- `symlink_docs/projects/itheraputix/` — iTheraputix project docs (PRD.md, DATABASE.md, etc.)

---

## Task Routing — Scan Before Any Work

| Task involves... | Project Guide | Global Skill |
|-----------------|---------------|--------------|
| KB commands, search, context, ingest | `plans/26-1-26-kb-guide.md` | kb |
| KB system architecture, CRUD functions, DB tables | `guides/26-3-7_GUIDE_kb-system-reference.md` | — |
| Railway deployment, auth, config abstraction | `guides/26-3-7_PCGUIDE_kb-railway-deployment.md` | devops |
| Dashboard API or frontend | `guides/26-3-7_GUIDE_kb-system-reference.md` | frontend, tailwind-theming |
| Stakeholder docs, synthesis, harvest | `stakeholders/itheraputix/*.md` | kb |
| iTheraputix product/roadmap context | `projects/itheraputix/PRD.md` | — |
| Audio transcription, Whisper | `plans/26-2-4-kb-audio-learning-system.md` | kb |
| Planning, architecture design | — | planning |

---

## Critical Patterns (Always in Context)

These fire every session — too important to defer to guides.

- **LM Studio stays local** for batch summaries and filler classification (Mac Mini GPU, saves cloud tokens). Knowledge base Postgres is migrating to Railway for team access.
- **Two memory layers**: Database rows store discrete facts (decisions, questions, actions). Markdown docs store synthesized understanding (stakeholder motivations, patterns, mental models). Both are essential — DB is queryable, markdown is LLM-native context.
- **Stakeholder docs are durable artifacts** in `symlink_docs/stakeholders/itheraputix/`. They improve over time via KB harvest → human editorial judgment → markdown update. Never auto-overwrite — human curation is the value.
- **Config singleton**: `kb_config` table (CHECK id=1) stores LLM url/model, embed model/backend. `config.py` reads at import time.
- **ONNX embeddings only**: `packages/embed/` uses nomic-embed-text-v1.5 via ONNX Runtime. No torch dependency.

---

## KB CLI Quick Reference

Entry point: `uv run python -m scripts.kb_cli <command>` (aliased as `kb`)

**Common user requests → commands:**
- "Search kb for [topic]" → `kb search "topic" -c org -p project`
- "Show me all stakeholders" → `kb list-org`
- "What calls did I have with [name]?" → `kb list-calls -c "org"`
- "Analyze call [id]" → `kb peterson-analyze <id>`
- "Summarize call [id]" → `kb summarize <id>`
- "Show summaries for call [id]" → `kb show-summaries <id>`

Full command reference: `symlink_docs/guides/26-3-7_GUIDE_kb-system-reference.md`

---

## Dashboard

React + Tailwind v4 + FastAPI. Main view: **Roadmap Kanban**. API supports read + write. Details in system reference guide.

**Starting:**
- API: `cd ~/repos/utilities && uv run uvicorn dashboard.api.main:app --port 8006 --reload`
- Frontend: `cd ~/repos/utilities/dashboard/frontend && npm run dev`
- Verify: `curl -s http://localhost:8006/health`

---

## Railway

**Deploy method:** `railway up` (static site exception — see `railway-patterns.md` § Static Site Exception)
**API keys:** `~/.config/keys.json` → `railway` (see global CLAUDE.md)
**Patterns:** `@~/repo_docs/skills/devops/railway-patterns.md`

| Key | Value |
|-----|-------|
| Project | `kb` |
| Project ID | `a3677be5-5392-473e-b609-f23b7c06b78c` |
| Production Env ID | `3317309b-8f0c-43f4-9d8a-73b1c9fecf9c` |

### Services

| Service | Service ID | Purpose |
|---------|-----------|---------|
| hj-roadmap | `5d97ef67-1434-487a-9069-df8b98a0dd95` | MkDocs roadmap site + feedback API (OTP auth) |
| Postgres | `ae33aa6f-3890-4af7-aec6-13904be1c242` | knowledge_base DB (shared with KB pipeline) |

### HJ Roadmap Deploy

Source: `repo_docs/utilities/plans/hj_roadmap/`

```bash
cd ~/repo_docs/utilities/plans/hj_roadmap
mkdocs build -f mkdocs.yml -d site/doctor
mkdocs build -f mkdocs-dme.yml -d site/dme
mkdocs build -f mkdocs-pt.yml -d site/pt
railway up
```

All other Railway operations (vars, redeploy, rollback, domains) use GraphQL API per `railway-patterns.md`.

---

## Reference Links

- **Guides**: `symlink_docs/guides/` — GUIDE (reference), PCGUIDE (supports CLAUDE.md patterns)
- **Plans**: `symlink_docs/plans/` — KB guide, audio learning system, FDIC monitor, TeamHub
- **Stakeholder docs**: `symlink_docs/stakeholders/itheraputix/` — doctor, patient, PT, DME, investor
- **Project docs**: `symlink_docs/projects/itheraputix/` — PRD, DATABASE, RESTART
- **Own project docs**: `symlink_docs/project/` — utilities PROJECT, ARCHITECTURE, RESTART
