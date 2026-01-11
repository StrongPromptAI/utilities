Create a staging environment on Railway for mainstreak before production.

**Goal**: Smoke test deploys in staging environment that mirrors production.

**Tasks**:
1. Create Railway project for mainstreak-staging
2. Link staging database (separate from prod)
3. Set up staging environment variables (API keys, feature flags)
4. Configure domain: staging.mainstreak.com or similar
5. Set up automated deploys from staging branch
6. Create deployment checklist: what tests to run before promoting to prod
7. Document rollback procedure if staging deploy fails

**Success**: Can deploy to staging, verify smoke tests pass, then confidently deploy to production.