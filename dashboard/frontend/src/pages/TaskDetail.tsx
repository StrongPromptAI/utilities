import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { api } from "../api";
import type { ActionItem } from "../types";
import { CopyButton } from "../components/CopyButton";

function formatDate(iso: string): string {
  return iso.slice(0, 10);
}

export function TaskDetail() {
  const { id } = useParams<{ id: string }>();
  const [item, setItem] = useState<ActionItem | null>(null);
  const [prompt, setPrompt] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    const numId = Number(id);
    api.action(numId).then(setItem).catch(() => setError("Task not found"));
    api.actionPrompt(numId).then(setPrompt).catch(() => setPrompt(null));
  }, [id]);

  if (error) {
    return (
      <div className="detail-page">
        <Link to="/">&larr; Dashboard</Link>
        <p className="muted">{error}</p>
      </div>
    );
  }

  if (!item) {
    return (
      <div className="detail-page">
        <p className="muted">Loading...</p>
      </div>
    );
  }

  return (
    <div className="detail-page">
      <Link to="/">&larr; Dashboard</Link>
      <h1>
        <span className={`status-badge ${item.status === "done" ? "confirmed" : "open"}`}>
          {item.status}
        </span>
        {item.title}
      </h1>

      <dl>
        <dt>Assigned to</dt>
        <dd>{item.assigned_to ?? "Unassigned"}</dd>

        <dt>Created</dt>
        <dd>{formatDate(item.created_at)}</dd>

        {item.completed_at && (
          <>
            <dt>Completed</dt>
            <dd>{formatDate(item.completed_at)}</dd>
          </>
        )}

        {item.prompt_file && (
          <>
            <dt>Prompt</dt>
            <dd>{item.prompt_file}</dd>
          </>
        )}
      </dl>

      {item.description && (
        <section>
          <h2>Description</h2>
          <p style={{ whiteSpace: "pre-wrap" }}>{item.description}</p>
        </section>
      )}

      {item.question_id && (
        <section>
          <h2>Linked Question</h2>
          <Link to={`/questions/${item.question_id}`} target="_blank">
            {item.question_topic ?? `Question ${item.question_id}`}
            <span className={`kanban-tag ${item.question_status === "open" ? "draft" : ""}`} style={{ marginLeft: "0.5rem" }}>
              {item.question_status === "open" ? "draft" : item.question_status}
            </span>
          </Link>
        </section>
      )}

      {prompt && (
        <section>
          <div className="prompt-header">
            <h2>Prompt</h2>
            <CopyButton text={prompt} label="Copy prompt" />
          </div>
          <pre className="prompt-content">{prompt}</pre>
        </section>
      )}
    </div>
  );
}
