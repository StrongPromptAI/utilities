import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import type { OpenQuestion } from "../types";
import { CopyButton } from "../components/CopyButton";

/** Backward compat: /decisions/:id redirects to question view */
export function DecisionDetail() {
  const { id } = useParams();
  const [item, setItem] = useState<OpenQuestion | null>(null);

  useEffect(() => {
    if (id) api.question(Number(id)).then(setItem);
  }, [id]);

  if (!item) return <div className="detail-page">Loading...</div>;

  const label = item.status === "decided" ? "Decision" : "Question";

  return (
    <div className="detail-page">
      <h1>
        {label} #{item.id}
        <CopyButton text={`[Q${item.id}] ${item.topic}: ${item.resolution || item.question}`} />
      </h1>
      <dl>
        <dt>Topic</dt>
        <dd>{item.topic}</dd>
        <dt>Status</dt>
        <dd><span className={`status-badge ${item.status}`}>{item.status}</span></dd>
        <dt>Question</dt>
        <dd>{item.question}</dd>
        {item.resolution && (
          <>
            <dt>Resolution</dt>
            <dd>{item.resolution}</dd>
          </>
        )}
        {item.context && (
          <>
            <dt>Context</dt>
            <dd>{item.context}</dd>
          </>
        )}
        {item.decided_by && item.decided_by.length > 0 && (
          <>
            <dt>Decided by</dt>
            <dd>{item.decided_by.map((c) => c.name).join(", ")}</dd>
          </>
        )}
        {item.owner_name && (
          <>
            <dt>Owner</dt>
            <dd>{item.owner_name}</dd>
          </>
        )}
        {item.source_call_id && (
          <>
            <dt>Source call</dt>
            <dd>{item.source_call_id}</dd>
          </>
        )}
        <dt>Created</dt>
        <dd>{item.created_at}</dd>
        {item.updated_at && (
          <>
            <dt>Updated</dt>
            <dd>{item.updated_at}</dd>
          </>
        )}
      </dl>
    </div>
  );
}
