Research and plan Vercel → Railway migration strategy:

**Goal**: Identify all sites still on Vercel, migrate to Railway, keep deployment code DRY by leveraging CLI similarity.

**Part 1: Inventory Current Vercel Deployments**
- Check Vercel dashboard or CLI: `vercel list` (if available)
- Audit these repos for Vercel references:
  - .vercel/ folders
  - vercel.json config files
  - package.json scripts with "vercel" in them
  - GitHub Actions workflows deploying to Vercel
  - DNS/domain configs pointing to Vercel
- Document for each site:
  - Project name
  - Framework/type (Next.js, static site, API, etc.)
  - Current Vercel config (vercel.json if exists)
  - Environment variables in Vercel
  - Current domain(s)
  - Build/deploy requirements

**Part 2: Research Vercel CLI vs Railway CLI Similarity**
- **Vercel CLI comparison**:
  - Command structure: `vercel [command] [options]`
  - Key commands: `vercel login`, `vercel link`, `vercel deploy`, `vercel env`, `vercel logs`
  - Config file: `vercel.json`
  - Authentication: `~/.vercel/auth.json`
- **Railway CLI patterns** (from existing push/railway.md skill):
  - Command structure: `railway [command]`
  - Key commands: `railway link`, `railway up`, `railway variables`, `railway logs`, `railway domain`
  - Config file: `.railwayrc`
  - Authentication: `~/.railway/config.json`
- **Analyze similarities**:
  - Do both support same authentication flow? (token-based)
  - Similar deploy syntax? (push-to-deploy vs explicit deploy command)
  - How do env vars work in each?
  - Domain/custom domain setup differences
  - Logging and monitoring capabilities
  - Build configuration options
- **Find common abstraction** (for code reuse):
  - Can we create wrapper script that abstracts platform differences?
  - Example: `deploy.sh --platform=railway` vs `deploy.sh --platform=vercel`
  - Or should each platform have own script in push/vercel.md skill?

**Part 3: Create Migration Plan**
- For each site:
  - Step 1: Set up Railway project
  - Step 2: Migrate environment variables
  - Step 3: Configure domain/DNS
  - Step 4: Verify deployment works
  - Step 5: Cut over traffic
  - Step 6: Decommission Vercel project
- Identify any blockers (Vercel-specific features, costs, team permissions)

**Part 4: Plan push/vercel.md Skill** (future, for comparison)
- Document Vercel deployment patterns (similar to push/railway.md)
- Include: CLI reference, environment setup, domain config, logging
- Note similarities/differences with Railway for developers
- Consider: Is push/vercel.md needed long-term or just migration guide?

**Part 5: DRY Deployment Code Strategy**
- Option A: Single abstraction layer
  - `push/deploy.sh --platform=railway|vercel [options]`
  - Pros: One mental model, code reuse
  - Cons: Adds abstraction overhead
- Option B: Parallel skills
  - `push/railway.md` (maintained)
  - `push/vercel.md` (new, for comparison)
  - Pros: Each platform fully documented
  - Cons: Code duplication
- Option C: Hybrid
  - push/railway.md fully featured
  - push/vercel.md minimal ("use Railway instead, here's migration guide")
  - Pros: Clear migration path, minimal maintenance
  - Cons: Assumes Vercel becomes legacy

Result: Complete inventory of Vercel sites, CLI comparison docs, migration playbook, decision on code reuse strategy.