Execute local development testing for PT Assistant POC using native stack (Homebrew Postgres + LM Studio). Validate TKR (total knee replacement) RAG chat workflow with real medical records.

**Goal**: Verify end-to-end local testing works: services → data sync → onboarding → RAG chat → logging.

**Reference Plan**: `/Users/metatron3/repos/lora_training/symlink_docs/plans/local-dev-test-plan-poc.md`

**Duration**: ~20-30 minutes

**Environment**: Native (NO Docker) - Postgres + LM Studio on local Mac

---

## Quick Execution Overview

**Part 1: Start Services (3 terminals)**
1. Terminal 1: Verify PostgreSQL running (`brew services start postgresql@17`)
2. Terminal 2: Open LM Studio, load model (qwen2.5-coder:7b), verify port 1234
3. Terminal 3: Start FastAPI backend (`cd backend-lmstudio && uv run uvicorn app.main:app --reload --port 8000`)
4. Health check: `curl -s http://127.0.0.1:8000/health | jq .`

**Part 2: Seed Database via MCP**
- Add 3 providers (2 doctors + Newport Beach MRI imaging center)
- Verify patients, reports, report_chunks loaded
- Sync exercises from Railway via MCP (should have 50+)
- Sync ICD-10 codes from Railway via MCP (should have 70,000+)

**Part 3: Test Onboarding**
- Request magic link for test email
- Retrieve token from database (no email in dev)
- Verify token → get session token

**Part 4: TKR Chat Workflow**
- Non-RAG baseline: Ask "What is total knee replacement?" (verify LM Studio responding)
- RAG chat #1: "What does MRI show about the patient's knee?" (verify rag_chunks_used > 0)
- RAG chat #2: "What surgical technique was used?" (verify operative note chunks)
- RAG chat #3: Multi-turn synthesis (combine MRI + operative findings)

**Part 5: Verify Logging**
- Check chat_logs table: 4+ entries for test patient
- Verify rag_enabled = true for RAG queries
- Confirm responses include patient-specific data (not generic)

**Part 6: Document Results**
- Create result file: `symlink_docs/deployment-logs/local-dev-[DATE].md`
- Checklist: Services, Database, Onboarding, Chat workflow, Logging
- Record: Response times, chunk counts, any issues

---

## Key Checkpoints (Success Criteria)

**Database Setup** ✓
- [ ] Patients loaded (12+)
- [ ] 3 providers created (Dr. Chen, Dr. Williams, Newport Beach MRI)
- [ ] Exercises synced from Railway (50+)
- [ ] ICD-10 codes synced from Railway (70,000+)
- [ ] Reports exist (MRI + operative notes for TKR)
- [ ] Report chunks indexed (100+)

**Onboarding** ✓
- [ ] Magic link workflow completes
- [ ] Session token obtained
- [ ] Backend health check passes

**TKR Chat Workflow** ✓
- [ ] Non-RAG baseline chat works (LM Studio responding, generic answer)
- [ ] RAG chat #1: MRI findings (rag_chunks_used > 0, response includes imaging data)
- [ ] RAG chat #2: Operative report (rag_chunks_used > 0, response includes surgical details)
- [ ] RAG chat #3: Multi-turn synthesis (response combines MRI + operative context)

**Logging** ✓
- [ ] All 4+ chats logged in chat_logs table
- [ ] RAG metadata stored (rag_enabled = true, rag_chunks_used recorded)
- [ ] Responses include actual patient data (not boilerplate)

---

## Quick Test Script (Optional, Use if Manual Timing is Tight)

The plan includes `symlink_docs/local-dev-test.sh` (Part 9) that automates most of this:

```bash
chmod +x symlink_docs/local-dev-test.sh
./symlink_docs/local-dev-test.sh
```

This will:
1. Verify health
2. Create test patient
3. Seed test report with chunks
4. Request magic link
5. Test RAG chat
6. Verify logging

---

## Troubleshooting Quick Reference

**Backend won't start**: `lsof -i :8000` → kill if in use → restart

**DB connection refused**: `brew services start postgresql@17` → verify with `pg_isready -U $(whoami)`

**LM Studio not connecting**: `curl http://localhost:1234/v1/models` → Open LM Studio manually, load model, enable server

**Magic link not working**: Run migrations: `uv run alembic upgrade head`

**RAG not using patient data**: Verify patient exists, report exists, chunks exist (check SQL queries in plan Part 10)

---

## Result Documentation

After testing completes, create: `symlink_docs/deployment-logs/local-dev-[DATE].md`

Use template from plan Part 11. Include:
- Services started (checkboxes)
- Database setup results (counts)
- Onboarding results
- TKR chat workflow results (rag_chunks_used numbers)
- Any issues encountered
- Response times, duration

---

## Files to Reference During Test

- **Plan**: `/Users/metatron3/repos/lora_training/symlink_docs/plans/local-dev-test-plan-poc.md` (full details)
- **Script** (optional): `/Users/metatron3/repos/lora_training/symlink_docs/local-dev-test.sh` (Part 9 of plan)
- **Results** (after): Create in `/Users/metatron3/repos/lora_training/symlink_docs/deployment-logs/`

---

## Success = All Checkpoints Green

Test is complete when:
1. Services running natively without Docker
2. All database tables populated (patients, providers, reports, chunks, exercises, icd10)
3. Magic link onboarding works (session token obtained)
4. 3+ RAG chats execute, returning patient-specific data from MRI + operative notes
5. All chats logged with rag metadata
6. Result file created with checklist and observations

**Expected Time**: 20-30 minutes (includes service startup, DB sync, testing, documentation)