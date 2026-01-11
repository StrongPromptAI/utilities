Integrate PROJECT_MATURITY tracking with planning process so plans automatically adjust scope expectations based on project phase (POC/MVP/PROD).

**Goal**: Planning decisions respect the Spry Design principle from golden-stack. Different phases justify different rigor levels.

**Context**:
- Golden-stack/SKILL.md already defines phases (Smoke Test, POC, MVP, Production) with clear include/skip patterns
- Planning/SKILL.md references golden-stack but doesn't explicitly use phase info during plan creation
- Projects have implicit phases but no standardized way to declare maturity
- Result: Plans sometimes over-engineer POCs or under-engineer MVPs

---

## Part 1: Update planning/SKILL.md

1. **Add "Project Maturity" section** (new, after "Core Principles" and before "Plan Template")

Content:
```markdown
### 5. Align with Project Maturity (Spry Design)

Every plan must declare the project's current maturity phase and adjust scope accordingly.

Reference: [`golden-stack/SKILL.md` Principle #6: Spry Design](../golden-stack/SKILL.md#6-spry-design)

**Why**: A POC doesn't need security audits. An MVP doesn't need speculative features. Production needs both.

| Phase | Scope Focus | Skip | Reference |
|-------|------------|------|----------|
| **Smoke Test** | Prove core happy path works | Security, observability | Quick validation |
| **POC** | Demonstrate feature, get stakeholder buy-in | Scalability, prod patterns | Time-boxed exploration |
| **MVP** | Deliver only must-haves to real users | Nice-to-haves, future tech | Viable but minimal |
| **Production** | Security, monitoring, performance, docs | Speculative features | Battle-ready |

**In your plan**:
- Add a **"Project Maturity"** field at the top
- Example: `Project Maturity: MVP` (not "Phase 1", which is execution)
- Use this to guide what goes in/out of Scope section
- Reference in each phase: "Aligned with MVP: includes audit logging (must-have), skips rate limiting (future)"
```

2. **Update plan template** to include maturity field:

Find the template section (around line 131) and update frontmatter:
```markdown
---
status: draft
project_maturity: MVP  # NEW: Smoke Test, POC, MVP, or Production
created: 2026-01-10T09:30:00-07:00
updated: 2026-01-10T14:45:00-07:00
effort: medium
risk: low
---
```

3. **Update template Scope section** to reference maturity:

After Scope table, add:
```markdown
**Maturity alignment**: This plan targets **MVP** maturity (per golden-stack Principle #6).
- ✅ Includes: Audit logging (must-have for user trust)
- ✅ Includes: Error handling (core stability)
- ❌ Skips: Advanced 2FA (future, not MVP)
- ❌ Skips: Rate limiting (production-only)
```

---

## Part 2: Add PROJECT_MATURITY to All Projects

### Create .env.example template for each project

Add to every repo (lora_training, utilities, burntfork-retreat-showcase, strongprompt-website, mainstreak):

**File**: `.env.example` (if doesn't exist) or add line to existing

```bash
# Project maturity phase - used by planning/skills to adjust scope
# Values: Smoke Test, POC, MVP, Production
PROJECT_MATURITY=MVP  # default, update when phase changes
```

**File**: `.env` (in each repo, .gitignored)

```bash
PROJECT_MATURITY=MVP
```

### Update CLAUDE.md in each project

Add section after "Verify Symlinks":

```markdown
### Project Maturity

This project is currently: **\`${PROJECT_MATURITY}\`**

- **POC**: Exploring architecture, gathering feedback, time-boxed
- **MVP**: Shipped to users, core features only, stable enough for daily use
- **Production**: Battle-ready, security, monitoring, performance optimized

Plans reference this phase to adjust scope via [golden-stack Principle #6: Spry Design](~/repo_docs/skills/golden-stack/SKILL.md#6-spry-design).

If phase changes, update `.env` and notify team:
```bash
echo "PROJECT_MATURITY=Production" >> .env
git add .env.example CLAUDE.md
git commit -m "Promote project to Production maturity"
```
```

---

## Part 3: Integration with Planning Process

When creating a plan:

1. **Read PROJECT_MATURITY** (from .env or ask user): "This project is POC maturity."

2. **Use to guide Scope section**:
   - If POC: "Skip scalability testing (not needed yet)"
   - If MVP: "Include user testing (must validate with real users)"
   - If Production: "Include security review (required for this phase)"

3. **Validate phases against maturity**:
   - POC plan should not include "Phase 5: Monitoring Setup" (premature)
   - MVP plan should include "Phase N: User Testing" (critical)
   - Production plan should include "Phase N: Security Audit" (non-negotiable)

4. **Add to plan Skills Required**:
   - Reference [`golden-stack/SKILL.md` #6](../golden-stack/SKILL.md#6-spry-design) in every plan
   - Include it in the skills table as context

---

## Part 4: Projects to Update

**Immediate**:
- [ ] Update `planning/SKILL.md`: Add "Project Maturity" principle + template field + maturity alignment in Scope
- [ ] lora_training: Add `.env.example` + `.env` with PROJECT_MATURITY=MVP, update CLAUDE.md
- [ ] utilities: Add `.env.example` + `.env` with PROJECT_MATURITY=MVP, update CLAUDE.md

**Follow-up** (coordinate with team):
- [ ] burntfork-retreat-showcase: Determine maturity (POC? MVP?), add declarations
- [ ] strongprompt-website: Determine maturity, add declarations
- [ ] mainstreak: Determine maturity, add declarations

---

## Part 5: Documentation

Create brief guide: `symlink_docs/guides/project-maturity-guide.md`

Content:
- What each phase means
- When to promote (smoke test → POC → MVP → Prod)
- How to update PROJECT_MATURITY
- Examples: What MVP lora_training skips vs includes
- Link to golden-stack Principle #6

---

## Success Criteria

- [ ] planning/SKILL.md updated with "Project Maturity" section
- [ ] planning/SKILL.md template includes `project_maturity` field
- [ ] All active projects have `.env.example` with PROJECT_MATURITY documented
- [ ] All active projects have `.env` with current PROJECT_MATURITY set
- [ ] All active projects' CLAUDE.md explains maturity and links to golden-stack #6
- [ ] Next new plan created will reference PROJECT_MATURITY in Scope section
- [ ] Project maturity guide created in symlink_docs/guides/

**Result**: Planning process now understands project phase and automatically adjusts rigor expectations (Spry Design from golden-stack).