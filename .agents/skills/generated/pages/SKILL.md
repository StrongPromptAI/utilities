---
name: pages
description: "Skill for the Pages area of utilities. 10 symbols across 8 files."
---

# Pages

10 symbols | 8 files | Cohesion: 72%

## When to Use

- Working with code in `dashboard/`
- Understanding how TaskDetail, QuestionDetail, DecisionDetail work
- Modifying pages-related functionality

## Key Files

| File | Symbols |
|------|---------|
| `dashboard/frontend/src/pages/TaskDetail.tsx` | formatDate, TaskDetail |
| `dashboard/frontend/src/components/OpenQuestionsPanel.tsx` | questionDisplayStatus, OpenQuestionsPanel |
| `dashboard/frontend/src/pages/QuestionDetail.tsx` | QuestionDetail |
| `dashboard/frontend/src/pages/DecisionDetail.tsx` | DecisionDetail |
| `dashboard/frontend/src/pages/ClientDetail.tsx` | ClientDetail |
| `dashboard/frontend/src/pages/CallDetail.tsx` | CallDetail |
| `dashboard/frontend/src/components/CopyButton.tsx` | CopyButton |
| `dashboard/frontend/src/components/ClustersPanel.tsx` | ClustersPanel |

## Entry Points

Start here when exploring this area:

- **`TaskDetail`** (Function) — `dashboard/frontend/src/pages/TaskDetail.tsx:10`
- **`QuestionDetail`** (Function) — `dashboard/frontend/src/pages/QuestionDetail.tsx:6`
- **`DecisionDetail`** (Function) — `dashboard/frontend/src/pages/DecisionDetail.tsx:7`
- **`ClientDetail`** (Function) — `dashboard/frontend/src/pages/ClientDetail.tsx:6`
- **`CallDetail`** (Function) — `dashboard/frontend/src/pages/CallDetail.tsx:6`

## Key Symbols

| Symbol | Type | File | Line |
|--------|------|------|------|
| `TaskDetail` | Function | `dashboard/frontend/src/pages/TaskDetail.tsx` | 10 |
| `QuestionDetail` | Function | `dashboard/frontend/src/pages/QuestionDetail.tsx` | 6 |
| `DecisionDetail` | Function | `dashboard/frontend/src/pages/DecisionDetail.tsx` | 7 |
| `ClientDetail` | Function | `dashboard/frontend/src/pages/ClientDetail.tsx` | 6 |
| `CallDetail` | Function | `dashboard/frontend/src/pages/CallDetail.tsx` | 6 |
| `OpenQuestionsPanel` | Function | `dashboard/frontend/src/components/OpenQuestionsPanel.tsx` | 18 |
| `CopyButton` | Function | `dashboard/frontend/src/components/CopyButton.tsx` | 2 |
| `ClustersPanel` | Function | `dashboard/frontend/src/components/ClustersPanel.tsx` | 6 |
| `formatDate` | Function | `dashboard/frontend/src/pages/TaskDetail.tsx` | 6 |
| `questionDisplayStatus` | Function | `dashboard/frontend/src/components/OpenQuestionsPanel.tsx` | 14 |

## Connected Areas

| Area | Connections |
|------|-------------|
| Components | 2 calls |

## How to Explore

1. `gitnexus_context({name: "TaskDetail"})` — see callers and callees
2. `gitnexus_query({query: "pages"})` — find related execution flows
3. Read key files listed above for implementation details
