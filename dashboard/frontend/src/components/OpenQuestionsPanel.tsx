import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { OpenQuestion } from "../types";
import { Panel } from "./Panel";
import { CopyButton } from "./CopyButton";

const STATUSES: { value: string; label: string }[] = [
  { value: "", label: "all" },
  { value: "open", label: "unresolved" },
  { value: "answered", label: "answered" },
  { value: "abandoned", label: "abandoned" },
];

function questionDisplayStatus(dbStatus: string): string {
  return dbStatus === "open" ? "unresolved" : dbStatus;
}

export function OpenQuestionsPanel({ projectId }: { projectId: number | null }) {
  const [items, setItems] = useState<OpenQuestion[]>([]);
  const [status, setStatus] = useState("");

  useEffect(() => {
    if (!projectId) {
      setItems([]);
      return;
    }
    api.questions(projectId, status || undefined).then(setItems).catch(() => setItems([]));
  }, [projectId, status]);

  const filters = (
    <div className="status-pills">
      {STATUSES.map((s) => (
        <button
          key={s.value}
          className={`pill ${status === s.value ? "active" : ""}`}
          onClick={() => setStatus(s.value)}
        >
          {s.label}
        </button>
      ))}
    </div>
  );

  return (
    <Panel title="Unanswered Questions" actions={filters}>
      {!projectId && <p className="muted">Select a project</p>}
      {projectId && items.length === 0 && <p className="muted">No questions</p>}
      <ul className="item-list">
        {items.map((q) => (
          <li key={q.id} className="question-item">
            <CopyButton text={`[Question ${q.id}] ${q.topic}: ${q.question}`} />
            <Link to={`/questions/${q.id}`} target="_blank">
              <div className="item-top">
                <span className={`status-badge ${q.status}`}>{questionDisplayStatus(q.status)}</span>
                <strong>{q.topic}</strong>
              </div>
              <div className="item-body">{q.question}</div>
              {q.context && <div className="item-context">Why it matters: {q.context}</div>}
              {q.owner && <div className="item-meta">Owner: {q.owner}</div>}
            </Link>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
