# devops_agent — DEPRECATED (2026-06-05)

**Status:** Paused, not deleted. Too early — same call as THJ's Conversation Factory: the idea is sound but it got built ahead of a real, recurring need.

**Do not build on this now.** It is not wired into any live workflow. The code is kept intact because the structure (Click CLI scaffold, Railway status/health/rollback/smoke/validate commands, TOML project config) may be worth rehabilitating once there's a concrete, repeated DevOps automation need to justify it.

**For Railway ops today:** use the GraphQL API recipes in the repo `CLAUDE.md` § Railway (auth, pull vars, deploy status). No CLI/agent layer.

**If rehabilitating:** revisit whether each command earns its place against the current phase (Spry Design), and reconcile against the deploy-automation that exists by then before resurrecting wholesale.
