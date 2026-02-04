import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { Decision, OpenQuestion } from "../types";
import { Panel } from "./Panel";
import { CopyButton } from "./CopyButton";

interface Props {
  projectId: number | null;
}

/** Merge draft decisions + open questions into a single "under discussion" list */
type UnresolvedItem =
  | { kind: "decision"; data: Decision }
  | { kind: "question"; data: OpenQuestion };

type SortKey = "newest" | "oldest";

function formatDate(iso: string): string {
  return iso.slice(0, 10);
}

function sortItems<T extends { created_at: string }>(items: T[], sort: SortKey): T[] {
  const copy = [...items];
  if (sort === "oldest") return copy.sort((a, b) => a.created_at.localeCompare(b.created_at));
  return copy.sort((a, b) => b.created_at.localeCompare(a.created_at));
}

function sortUnresolved(items: UnresolvedItem[], sort: SortKey): UnresolvedItem[] {
  const copy = [...items];
  if (sort === "oldest") return copy.sort((a, b) => a.data.created_at.localeCompare(b.data.created_at));
  return copy.sort((a, b) => b.data.created_at.localeCompare(a.data.created_at));
}

export function DecisionsPanel({ projectId }: Props) {
  const [confirmed, setConfirmed] = useState<Decision[]>([]);
  const [unresolved, setUnresolved] = useState<UnresolvedItem[]>([]);
  const [showSuperseded, setShowSuperseded] = useState(false);
  const [sort, setSort] = useState<SortKey>("newest");

  useEffect(() => {
    if (!projectId) {
      setConfirmed([]);
      setUnresolved([]);
      return;
    }

    Promise.all([
      api.decisions(projectId),
      api.questions(projectId),
    ]).then(([decisions, questions]) => {
      setConfirmed(decisions.filter((d) => d.status === "confirmed" || d.status === "superseded"));

      const items: UnresolvedItem[] = [
        ...decisions.filter((d) => d.status === "open").map((d) => ({ kind: "decision" as const, data: d })),
        ...questions.filter((q) => q.status === "open").map((q) => ({ kind: "question" as const, data: q })),
      ];
      setUnresolved(items);
    }).catch(() => {
      setConfirmed([]);
      setUnresolved([]);
    });
  }, [projectId]);

  const sortedUnresolved = useMemo(() => sortUnresolved(unresolved, sort), [unresolved, sort]);

  const confirmedBase = showSuperseded
    ? confirmed
    : confirmed.filter((d) => d.status === "confirmed");
  const sortedConfirmed = useMemo(() => sortItems(confirmedBase, sort), [confirmedBase, sort]);

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
            <span className="kanban-count">{sortedUnresolved.length}</span>
          </div>
          {sortedUnresolved.length === 0 && <p className="muted">Nothing unresolved</p>}
          {sortedUnresolved.map((item) =>
            item.kind === "decision" ? (
              <div key={`d-${item.data.id}`} className="kanban-card draft">
                <CopyButton text={`[Decision ${item.data.id}] ${item.data.topic}: ${item.data.summary}`} />
                <Link to={`/decisions/${item.data.id}`} target="_blank">
                  <span className="kanban-tag draft">draft decision</span>
                  <strong>{item.data.topic}</strong>
                  <p>{item.data.summary}</p>
                  <div className="kanban-meta">{formatDate(item.data.created_at)}</div>
                </Link>
              </div>
            ) : (
              <div key={`q-${item.data.id}`} className="kanban-card question">
                <CopyButton text={`[Question ${item.data.id}] ${item.data.topic}: ${item.data.question}`} />
                <Link to={`/questions/${item.data.id}`} target="_blank">
                  <span className="kanban-tag question">open question</span>
                  <strong>{item.data.topic}</strong>
                  <p>{item.data.question}</p>
                  {item.data.context && (
                    <div className="kanban-context">{item.data.context}</div>
                  )}
                  <div className="kanban-meta">
                    {formatDate(item.data.created_at)}
                    {item.data.owner && <> &middot; {item.data.owner}</>}
                  </div>
                </Link>
              </div>
            ),
          )}
        </div>

        {/* Right: Decided */}
        <div className="kanban-col">
          <div className="kanban-col-header decided">
            Decided
            <span className="kanban-count">{sortedConfirmed.length}</span>
            <button
              className="pill superseded-toggle"
              onClick={() => setShowSuperseded(!showSuperseded)}
            >
              {showSuperseded ? "hide superseded" : "show superseded"}
            </button>
          </div>
          {sortedConfirmed.length === 0 && <p className="muted">No confirmed decisions</p>}
          {sortedConfirmed.map((d) => (
            <div key={d.id} className={`kanban-card ${d.status}`}>
              <CopyButton text={`[Decision ${d.id}] ${d.topic}: ${d.summary}`} />
              <Link to={`/decisions/${d.id}`} target="_blank">
                {d.status === "superseded" && (
                  <span className="kanban-tag superseded">superseded</span>
                )}
                <strong>{d.topic}</strong>
                <p>{d.summary}</p>
                <div className="kanban-meta">
                  {formatDate(d.created_at)}
                  {d.decided_by && d.decided_by.length > 0 && (
                    <> &middot; {d.decided_by.join(", ")}</>
                  )}
                </div>
              </Link>
            </div>
          ))}
        </div>
      </div>
    </Panel>
  );
}
