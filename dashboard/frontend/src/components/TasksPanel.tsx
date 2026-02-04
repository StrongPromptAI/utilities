import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { ActionItem } from "../types";
import { Panel } from "./Panel";
import { CopyButton } from "./CopyButton";

function formatDate(iso: string): string {
  return iso.slice(0, 10);
}

export function TasksPanel({ projectId }: { projectId: number | null }) {
  const [items, setItems] = useState<ActionItem[]>([]);
  const [showDone, setShowDone] = useState(false);

  useEffect(() => {
    if (!projectId) {
      setItems([]);
      return;
    }
    api.actions(projectId).then(setItems).catch(() => setItems([]));
  }, [projectId]);

  const open = items.filter((i) => i.status === "open");
  const done = items.filter((i) => i.status === "done");

  // Group open items by assigned_to
  const byAssignee = new Map<string, ActionItem[]>();
  for (const item of open) {
    const key = item.assigned_to ?? "Unassigned";
    const list = byAssignee.get(key) ?? [];
    list.push(item);
    byAssignee.set(key, list);
  }
  // Sort assignees alphabetically, but "Unassigned" last
  const assignees = [...byAssignee.keys()].sort((a, b) => {
    if (a === "Unassigned") return 1;
    if (b === "Unassigned") return -1;
    return a.localeCompare(b);
  });

  const toggle = (
    <div className="status-pills">
      <button
        className={`pill ${!showDone ? "active" : ""}`}
        onClick={() => setShowDone(false)}
      >
        open ({open.length})
      </button>
      <button
        className={`pill ${showDone ? "active" : ""}`}
        onClick={() => setShowDone(true)}
      >
        done ({done.length})
      </button>
    </div>
  );

  return (
    <Panel title="Tasks" actions={toggle}>
      {!projectId && <p className="muted">Select a project</p>}

      {projectId && !showDone && (
        <div className="tasks-board">
          {assignees.length === 0 && <p className="muted">No open tasks</p>}
          {assignees.map((assignee) => (
            <div key={assignee} className="task-group">
              <div className="task-group-header">{assignee}</div>
              {byAssignee.get(assignee)!.map((item) => (
                <div key={item.id} className={`task-card ${item.decision_id ? "has-decision" : ""} ${item.prompt_file ? "has-prompt" : ""}`}>
                  <CopyButton text={`[Task ${item.id}] ${item.title}${item.assigned_to ? ` @${item.assigned_to}` : ""}`} />
                  <Link to={`/tasks/${item.id}`} target="_blank" className="task-content">
                    <div className="task-title">
                      {item.title}
                      {item.prompt_file && <span className="tag">prompt</span>}
                    </div>
                    {item.description && (
                      <div className="task-desc">{item.description}</div>
                    )}
                    {item.decision_id && (
                      <div className="task-decision-link">
                        Review: {item.decision_topic}
                        <span className={`kanban-tag ${item.decision_status === "open" ? "draft" : ""}`}>
                          {item.decision_status === "open" ? "draft" : item.decision_status}
                        </span>
                      </div>
                    )}
                    <div className="task-meta">{formatDate(item.created_at)}</div>
                  </Link>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}

      {projectId && showDone && (
        <div className="tasks-done">
          {done.length === 0 && <p className="muted">No completed tasks</p>}
          {done.map((item) => (
            <div key={item.id} className="task-card done">
              <CopyButton text={`[Task ${item.id}] ${item.title} (done)`} />
              <Link to={`/tasks/${item.id}`} target="_blank" className="task-content">
                <div className="task-title">
                  {item.title}
                  {item.prompt_file && <span className="tag">prompt</span>}
                </div>
                <div className="task-meta">
                  {item.completed_at ? formatDate(item.completed_at) : formatDate(item.created_at)}
                  {item.assigned_to && <> &middot; {item.assigned_to}</>}
                </div>
              </Link>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}
