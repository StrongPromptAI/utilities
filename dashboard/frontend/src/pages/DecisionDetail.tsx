import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import type { Decision } from "../types";
import { CopyButton } from "../components/CopyButton";

export function DecisionDetail() {
  const { id } = useParams();
  const [decision, setDecision] = useState<Decision | null>(null);

  useEffect(() => {
    if (id) api.decision(Number(id)).then(setDecision);
  }, [id]);

  if (!decision) return <div className="detail-page">Loading...</div>;

  return (
    <div className="detail-page">
      <h1>
        Decision #{decision.id}
        <CopyButton text={`[Decision ${decision.id}] ${decision.topic}: ${decision.summary}`} />
      </h1>
      <dl>
        <dt>Topic</dt>
        <dd>{decision.topic}</dd>
        <dt>Status</dt>
        <dd><span className={`status-badge ${decision.status}`}>{decision.status}</span></dd>
        <dt>Summary</dt>
        <dd>{decision.summary}</dd>
        {decision.decided_by && decision.decided_by.length > 0 && (
          <>
            <dt>Decided by</dt>
            <dd>{decision.decided_by.join(", ")}</dd>
          </>
        )}
        {decision.source_call_ids && decision.source_call_ids.length > 0 && (
          <>
            <dt>Source calls</dt>
            <dd>{decision.source_call_ids.join(", ")}</dd>
          </>
        )}
        <dt>Created</dt>
        <dd>{decision.created_at}</dd>
        {decision.updated_at && (
          <>
            <dt>Updated</dt>
            <dd>{decision.updated_at}</dd>
          </>
        )}
      </dl>
    </div>
  );
}
