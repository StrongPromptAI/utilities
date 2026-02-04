import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import type { CallDetail as CallDetailType } from "../types";
import { CopyButton } from "../components/CopyButton";

export function CallDetail() {
  const { id } = useParams();
  const [data, setData] = useState<CallDetailType | null>(null);

  useEffect(() => {
    if (id) api.call(Number(id)).then(setData);
  }, [id]);

  if (!data) return <div className="detail-page">Loading...</div>;

  const { call, participants, summaries, chunks } = data;

  return (
    <div className="detail-page">
      <h1>
        Call #{call.id} &mdash; {call.client_name}
        <CopyButton
          text={`[Call ${call.id}] ${call.call_date} ${call.client_name}: ${call.summary ?? ""}`}
        />
      </h1>

      <dl>
        <dt>Date</dt>
        <dd>{call.call_date}</dd>
        <dt>Client</dt>
        <dd>{call.client_name}</dd>
        {call.project_name && (
          <>
            <dt>Project</dt>
            <dd>{call.project_name}</dd>
          </>
        )}
        {call.summary && (
          <>
            <dt>Summary</dt>
            <dd>{call.summary}</dd>
          </>
        )}
        {call.user_notes && (
          <>
            <dt>Notes</dt>
            <dd>{call.user_notes}</dd>
          </>
        )}
      </dl>

      {participants.length > 0 && (
        <section>
          <h2>Participants</h2>
          <ul>
            {participants.map((p) => (
              <li key={p.id}>
                {p.name}
                {p.role && <span className="muted"> ({p.role})</span>}
              </li>
            ))}
          </ul>
        </section>
      )}

      {summaries.length > 0 && (
        <section>
          <h2>Batch Summaries</h2>
          {summaries.map((s) => (
            <div key={s.id} className="batch-summary">
              <strong>Batch {s.batch_idx}</strong>
              <p>{s.summary}</p>
            </div>
          ))}
        </section>
      )}

      {chunks.length > 0 && (
        <section>
          <h2>Chunks ({chunks.length})</h2>
          <div className="chunks-list">
            {chunks.map((ch) => (
              <div key={ch.id} className="chunk">
                <div className="chunk-header">
                  <span className="chunk-idx">#{ch.chunk_idx}</span>
                  {ch.speaker && <span className="speaker">{ch.speaker}</span>}
                  <CopyButton text={ch.text} />
                </div>
                <div className="chunk-text">{ch.text}</div>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
