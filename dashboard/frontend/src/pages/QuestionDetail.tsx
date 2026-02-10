import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import type { OpenQuestion } from "../types";
import { CopyButton } from "../components/CopyButton";

export function QuestionDetail() {
  const { id } = useParams();
  const [q, setQ] = useState<OpenQuestion | null>(null);

  useEffect(() => {
    if (id) api.question(Number(id)).then(setQ);
  }, [id]);

  if (!q) return <div className="detail-page">Loading...</div>;

  return (
    <div className="detail-page">
      <h1>
        Question #{q.id}
        <CopyButton text={`[Question ${q.id}] ${q.topic}: ${q.question}`} />
      </h1>
      <dl>
        <dt>Topic</dt>
        <dd>{q.topic}</dd>
        <dt>Status</dt>
        <dd><span className={`status-badge ${q.status}`}>{q.status}</span></dd>
        <dt>Question</dt>
        <dd>{q.question}</dd>
        {q.context && (
          <>
            <dt>Context</dt>
            <dd>{q.context}</dd>
          </>
        )}
        {q.owner && (
          <>
            <dt>Owner</dt>
            <dd>{q.owner}</dd>
          </>
        )}
        {q.resolution && (
          <>
            <dt>Resolution</dt>
            <dd>{q.resolution}</dd>
          </>
        )}
        {q.decided_by && q.decided_by.length > 0 && (
          <>
            <dt>Decided by</dt>
            <dd>{q.decided_by.map((c) => c.name).join(", ")}</dd>
          </>
        )}
        <dt>Created</dt>
        <dd>{q.created_at}</dd>
      </dl>
    </div>
  );
}
