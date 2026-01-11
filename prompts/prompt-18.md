Create a comprehensive Railway test plan for lora_training to verify deployment health, database connectivity, API endpoints, and recent fax OCR + hybrid search features.

**Goal**: Document a repeatable test procedure that validates staging before promoting to production.

**Context**:
- Last deployment: `afe0a90` (Jan 9 17:49) - Added fax_production module with OCR + hybrid search
- Environment: Railway (staging + production)
- Key services: backend, postgres, deepgram (voice)
- Recent additions: OCR confidence scoring, semantic + FTS search, report_chunks table

---

## Part 1: Test Plan Structure

Create a document: `symlink_docs/plans/railway-lora-training-test-plan.md`

Include sections:
1. **Pre-Test Setup** - Environment verification, staging environment switch
2. **Service Health Checks** - Database, backend, deployment status
3. **API Endpoint Tests** - Health checks, version endpoints
4. **Fax OCR Ingestion Tests** - Upload sample PDF, verify OCR, check confidence scores
5. **Hybrid Search Tests** - FTS queries, semantic queries, combined search
6. **Performance Baselines** - Response times, database query times
7. **Rollback Procedure** - How to revert if critical issues found
8. **Success Criteria** - What constitutes "ready for prod"
9. **Monitoring Dashboard** - What to watch in Railway UI during/after deploy

---

## Part 2: Test Commands & Scripts

### Pre-Test
```bash
# Switch to staging environment
railway environment staging

# Verify current context
railway status
# Expected: Project: lora_training, Environment: staging

# Get staging database URL
railway variables --service postgres | grep DATABASE_PUBLIC_URL
```

### Service Health
```bash
# Check deployment status
railway deployment list --limit 5
# Should show: latest deployment, status=success, no errors

# Check service logs for errors
railway logs --service backend --limit 50
# Look for: no ERROR, no FATAL, version startup messages

# Test database connectivity
psql [DATABASE_PUBLIC_URL] -c "SELECT version();"
# Should succeed, no "connection refused"

# Check tables exist
psql [DATABASE_PUBLIC_URL] -c "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename;"
# Should see: users, documents, report_chunks, embeddings, etc.
```

### API Endpoint Tests
```bash
# Get staging backend URL from Railway
BACKEND_URL=$(railway variables --service backend | grep RAILWAY_PUBLIC_URL | cut -d= -f2)

# Health check
curl -s "$BACKEND_URL/health" | jq .
# Expected: {"status": "ok"}

# Version endpoint
curl -s "$BACKEND_URL/version" | jq .
# Expected: semantic version from pyproject.toml
```

### Fax OCR Ingestion Test
```bash
# 1. Prepare test PDF (sample scanned medical document)
# Use: ~/repo_docs/lora_training/test_documents/sample_fax.pdf (or create one)

# 2. Upload to ingestion endpoint
curl -X POST "$BACKEND_URL/ingest/fax" \
  -F "file=@sample_fax.pdf" \
  -H "Authorization: Bearer [STAGING_API_KEY]" | jq .

# Expected response:
# {"document_id": "abc123", "ocr_confidence": 0.95, "chunks_extracted": 12, "embeddings_created": 12}

# 3. Verify in database
psql [DATABASE_PUBLIC_URL] -c "SELECT id, ocr_confidence, chunk_count FROM documents WHERE id='abc123' LIMIT 1;"

# 4. Check chunks
psql [DATABASE_PUBLIC_URL] -c "SELECT COUNT(*) as chunk_count, AVG(ocr_confidence) as avg_confidence FROM report_chunks WHERE document_id='abc123';"
# Expected: 12 chunks, avg_confidence ~0.95
```

### Hybrid Search Test
```bash
# 1. Full-text search
curl -s -X POST "$BACKEND_URL/search/fts" \
  -H "Content-Type: application/json" \
  -d '{"query": "patient history diabetes", "limit": 10}' | jq .
# Expected: Returns matching chunks, ranked by relevance

# 2. Semantic search (embedding-based)
curl -s -X POST "$BACKEND_URL/search/semantic" \
  -H "Content-Type: application/json" \
  -d '{"query": "What is the diagnosis?", "limit": 10, "threshold": 0.7}' | jq .
# Expected: Returns semantically similar chunks (even if wording differs)

# 3. Hybrid search (FTS + semantic combined)
curl -s -X POST "$BACKEND_URL/search/hybrid" \
  -H "Content-Type: application/json" \
  -d '{"query": "medication allergies", "fts_weight": 0.4, "semantic_weight": 0.6, "limit": 10}' | jq .
# Expected: Blended results from both FTS and semantic
```

### Performance Baseline
```bash
# Measure ingestion time
time curl -X POST "$BACKEND_URL/ingest/fax" \
  -F "file=@sample_fax.pdf" \
  -H "Authorization: Bearer [API_KEY]" > /dev/null
# Expected: < 5 seconds (OCR is the bottleneck)

# Measure search response time
time curl -s -X POST "$BACKEND_URL/search/hybrid" \
  -H "Content-Type: application/json" \
  -d '{"query": "patient history", "limit": 10}' > /dev/null
# Expected: < 500ms (should be fast with indexes)

# Database query time
psql [DATABASE_PUBLIC_URL] -c "EXPLAIN ANALYZE SELECT * FROM report_chunks WHERE document_id='abc123' LIMIT 10;"
# Check: sequential scan vs index scan (should use index)
```

### Rollback Procedure
```bash
# If critical issues found:

# 1. Stop current staging deployment
railway deployment redeploy --latest-successful

# 2. Or revert to previous commit
cd ~/repos/lora_training
git revert HEAD
git push

# 3. Monitor logs
railway logs --service backend --follow
```

---

## Part 3: Success Criteria

- [ ] All services deployed and healthy
- [ ] Database connectivity verified
- [ ] API endpoints responding (health, version)
- [ ] Fax OCR ingestion works end-to-end
- [ ] OCR confidence scores reasonable (>0.8)
- [ ] Chunks extracted and stored correctly
- [ ] FTS search returns relevant results
- [ ] Semantic search returns semantically similar results
- [ ] Hybrid search combines both modalities
- [ ] Performance baseline within expected ranges
- [ ] No errors in logs (only info/debug)
- [ ] Database indexes being used (no sequential scans)

---

## Part 4: Monitoring During Test

Keep Railway dashboard open:
- **Deployments tab**: Monitor latest deployment status
- **Logs tab**: Watch backend logs for errors
- **Metrics tab**: CPU, memory, network during search load
- **Database tab**: Connection count, query times

---

## Part 5: Test Data

Create sample test documents:
- `symlink_docs/test_documents/medical_fax_1.pdf` - Standard medical form
- `symlink_docs/test_documents/medical_fax_2.pdf` - Complex multi-page
- `symlink_docs/test_documents/medical_fax_3.pdf` - Poor OCR quality (test confidence scoring)

Each should have: Scanned medical document (or generated PDF with medical text), Mix of handwriting and typed text, Multiple sections (patient info, diagnosis, medications, notes)

---

## Part 6: Next Steps After Validation

Once staging test passes:
1. Document results in `symlink_docs/deployment-log/staging-[DATE].md`
2. Get stakeholder sign-off
3. Tag production release: `git tag -a v1.2.0-fax-ocr -m "Add fax OCR + hybrid search"`
4. Deploy to production: `railway environment production && git push`
5. Run same tests on production
6. Monitor for 24 hours before closing task

---

**Success**: Comprehensive, repeatable test plan that validates fax OCR + search deployment before production.