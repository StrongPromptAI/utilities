---
name: components
description: "Skill for the Components area of utilities. 18 symbols across 12 files."
---

# Components

18 symbols | 12 files | Cohesion: 78%

## When to Use

- Working with code in `dashboard/`
- Understanding how TasksPanel, SearchPanel, handleSearch work
- Modifying components-related functionality

## Key Files

| File | Symbols |
|------|---------|
| `dashboard/frontend/src/components/DecisionsPanel.tsx` | formatDate, sortItems, DecisionsPanel |
| `dashboard/frontend/src/components/TasksPanel.tsx` | formatDate, TasksPanel |
| `dashboard/frontend/src/components/SearchPanel.tsx` | SearchPanel, handleSearch |
| `dashboard/frontend/src/components/RoadmapCard.tsx` | RoadmapCard, cycleStatus |
| `dashboard/frontend/src/components/RoadmapBoard.tsx` | RoadmapBoard, addCard |
| `dashboard/frontend/src/components/RecentCallsPanel.tsx` | RecentCallsPanel |
| `dashboard/frontend/src/components/Panel.tsx` | Panel |
| `dashboard/frontend/src/components/ClientsPanel.tsx` | ClientsPanel |
| `dashboard/frontend/src/App.tsx` | App |
| `dashboard/frontend/src/components/ThemeSwitcher.tsx` | ThemeSwitcher |

## Entry Points

Start here when exploring this area:

- **`TasksPanel`** (Function) — `dashboard/frontend/src/components/TasksPanel.tsx:11`
- **`SearchPanel`** (Function) — `dashboard/frontend/src/components/SearchPanel.tsx:6`
- **`handleSearch`** (Function) — `dashboard/frontend/src/components/SearchPanel.tsx:13`
- **`RecentCallsPanel`** (Function) — `dashboard/frontend/src/components/RecentCallsPanel.tsx:7`
- **`Panel`** (Function) — `dashboard/frontend/src/components/Panel.tsx:9`

## Key Symbols

| Symbol | Type | File | Line |
|--------|------|------|------|
| `TasksPanel` | Function | `dashboard/frontend/src/components/TasksPanel.tsx` | 11 |
| `SearchPanel` | Function | `dashboard/frontend/src/components/SearchPanel.tsx` | 6 |
| `handleSearch` | Function | `dashboard/frontend/src/components/SearchPanel.tsx` | 13 |
| `RecentCallsPanel` | Function | `dashboard/frontend/src/components/RecentCallsPanel.tsx` | 7 |
| `Panel` | Function | `dashboard/frontend/src/components/Panel.tsx` | 9 |
| `DecisionsPanel` | Function | `dashboard/frontend/src/components/DecisionsPanel.tsx` | 23 |
| `ClientsPanel` | Function | `dashboard/frontend/src/components/ClientsPanel.tsx` | 7 |
| `App` | Function | `dashboard/frontend/src/App.tsx` | 8 |
| `ThemeSwitcher` | Function | `dashboard/frontend/src/components/ThemeSwitcher.tsx` | 7 |
| `ProjectSelector` | Function | `dashboard/frontend/src/components/ProjectSelector.tsx` | 8 |
| `DocsNav` | Function | `dashboard/frontend/src/components/DocsNav.tsx` | 3 |
| `RoadmapCard` | Function | `dashboard/frontend/src/components/RoadmapCard.tsx` | 23 |
| `cycleStatus` | Function | `dashboard/frontend/src/components/RoadmapCard.tsx` | 30 |
| `RoadmapBoard` | Function | `dashboard/frontend/src/components/RoadmapBoard.tsx` | 19 |
| `addCard` | Function | `dashboard/frontend/src/components/RoadmapBoard.tsx` | 39 |
| `formatDate` | Function | `dashboard/frontend/src/components/TasksPanel.tsx` | 7 |
| `formatDate` | Function | `dashboard/frontend/src/components/DecisionsPanel.tsx` | 13 |
| `sortItems` | Function | `dashboard/frontend/src/components/DecisionsPanel.tsx` | 17 |

## Execution Flows

| Flow | Type | Steps |
|------|------|-------|
| `App → CycleStatus` | cross_community | 4 |
| `App → AddCard` | cross_community | 3 |

## Connected Areas

| Area | Connections |
|------|-------------|
| Pages | 5 calls |

## How to Explore

1. `gitnexus_context({name: "TasksPanel"})` — see callers and callees
2. `gitnexus_query({query: "components"})` — find related execution flows
3. Read key files listed above for implementation details
