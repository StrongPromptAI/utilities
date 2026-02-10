import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { OpenQuestion } from "../types";
import { Panel } from "./Panel";
import { CopyButton } from "./CopyButton";

interface Props {
  projectId: number | null;
}

type SortKey = "newest" | "oldest";

function formatDate(iso: string): string {
  return iso.slice(0, 10);
}

function sortItems(items: OpenQuestion[], sort: SortKey): OpenQuestion[] {
  const copy = [...items];
  if (sort === "oldest") return copy.sort((a, b) => a.created_at.localeCompare(b.created_at));
  return copy.sort((a, b) => b.created_at.localeCompare(a.created_at));
}

export function DecisionsPanel({ projectId }: Props) {
  const [all, setAll] = useState<OpenQuestion[]>([]);
  const [showAbandoned, setShowAbandoned] = useState(false);
  const [sort, setSort] = useState<SortKey>("newest");

  useEffect(() => {
    if (!projectId) {
      setAll([]);
      return;
    }

    api.questions(projectId).then(setAll).catch(() => setAll([]));
  }, [projectId]);

  const open = useMemo(
    () => sortItems(all.filter((q) => q.status === "open"), sort),
    [all, sort],
  );
  const decided = useMemo(
    () => sortItems(all.filter((q) => q.status === "decided"), sort),
    [all, sort],
  );
  const answered = useMemo(
    () => sortItems(all.filter((q) => q.status === "answered"), sort),
    [all, sort],
  );
  const abandoned = useMemo(
    () => sortItems(all.filter((q) => q.status === "abandoned"), sort),
    [all, sort],
  );

  if (!projectId) {
    return (
      <Panel title="Decision Board">
        <p className="muted">Select a project</p>
      </Panel>
    );
  }

  const sortPills = (
    <div className="status-pills">
      {(["newest", "oldest"] as const).map((s) => (
        <button
          key={s}
          className={`pill ${sort === s ? "active" : ""}`}
          onClick={() => setSort(s)}
        >
          {s}
        </button>
      ))}
    </div>
  );

  return (
    <Panel title="Decision Board" actions={sortPills}>
      <div className="kanban">
        {/* Left: Under Discussion */}
        <div className="kanban-col">
          <div className="kanban-col-header discussion">
            Under Discussion
            <span className="kanban-count">{open.length}</span>
          </div>
          {open.length === 0 && <p className="muted">Nothing unresolved</p>}
          {open.map((q) => (
            <div key={q.id} className="kanban-card question">
              <CopyButton text={`[Q${q.id}] ${q.topic}: ${q.question}`} />
              <Link to={`/questions/${q.id}`} target="_blank">
                <span className="kanban-tag question">open</span>
                <strong>{q.topic}</strong>
                <p>{q.question}</p>
                {q.context && (
                  <div className="kanban-context">{q.context}</div>
                )}
                <div className="kanban-meta">
                  {formatDate(q.created_at)}
                  {q.owner_name && <> &middot; {q.owner_name}</>}
                </div>
              </Link>
            </div>
          ))}
        </div>

        {/* Right: Decided */}
        <div className="kanban-col">
          <div className="kanban-col-header decided">
            Decided
            <span className="kanban-count">{decided.length + answered.length}</span>
            {abandoned.length > 0 && (
              <button
                className="pill superseded-toggle"
                onClick={() => setShowAbandoned(!showAbandoned)}
              >
                {showAbandoned ? "hide abandoned" : `show abandoned (${abandoned.length})`}
              </button>
            )}
          </div>
          {decided.length === 0 && answered.length === 0 && <p className="muted">No resolved items</p>}
          {decided.map((q) => (
            <div key={q.id} className="kanban-card confirmed">
              <CopyButton text={`[Q${q.id}] ${q.topic}: ${q.resolution || q.question}`} />
              <Link to={`/questions/${q.id}`} target="_blank">
                <span className="kanban-tag draft">decided</span>
                <strong>{q.topic}</strong>
                <p>{q.resolution || q.question}</p>
                <div className="kanban-meta">
                  {formatDate(q.created_at)}
                  {q.decided_by && q.decided_by.length > 0 && (
                    <> &middot; {q.decided_by.map((c) => c.name).join(", ")}</>
                  )}
                </div>
              </Link>
            </div>
          ))}
          {answered.map((q) => (
            <div key={q.id} className="kanban-card confirmed">
              <CopyButton text={`[Q${q.id}] ${q.topic}: ${q.resolution || q.question}`} />
              <Link to={`/questions/${q.id}`} target="_blank">
                <span className="kanban-tag question">answered</span>
                <strong>{q.topic}</strong>
                <p>{q.resolution || q.question}</p>
                <div className="kanban-meta">{formatDate(q.created_at)}</div>
              </Link>
            </div>
          ))}
          {showAbandoned && abandoned.map((q) => (
            <div key={q.id} className="kanban-card superseded">
              <CopyButton text={`[Q${q.id}] ${q.topic}: ${q.question}`} />
              <Link to={`/questions/${q.id}`} target="_blank">
                <span className="kanban-tag superseded">abandoned</span>
                <strong>{q.topic}</strong>
                <p>{q.question}</p>
                <div className="kanban-meta">{formatDate(q.created_at)}</div>
              </Link>
            </div>
          ))}
        </div>
      </div>
    </Panel>
  );
}
