import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import type { ClientContext } from "../types";
import { CopyButton } from "../components/CopyButton";

export function ClientDetail() {
  const { name } = useParams();
  const [data, setData] = useState<ClientContext | null>(null);

  useEffect(() => {
    if (name) api.client(name).then(setData);
  }, [name]);

  if (!data) return <div className="detail-page">Loading...</div>;

  const { client, calls, all_chunks_count } = data;

  return (
    <div className="detail-page">
      <h1>
        {client.name}
        <CopyButton text={`${client.name} (${client.type})${client.organization ? ` — ${client.organization}` : ""}`} />
      </h1>

      <dl>
        <dt>Type</dt>
        <dd>{client.type}</dd>
        {client.organization && (
          <>
            <dt>Organization</dt>
            <dd>{client.organization}</dd>
          </>
        )}
        {client.notes && (
          <>
            <dt>Notes</dt>
            <dd>{client.notes}</dd>
          </>
        )}
        <dt>Total chunks</dt>
        <dd>{all_chunks_count}</dd>
      </dl>

      <section>
        <h2>Calls ({calls.length})</h2>
        <ul className="item-list">
          {calls.map((c) => (
            <li key={c.id}>
              <CopyButton
                text={`[Call ${c.id}] ${c.call_date} ${c.client_name}: ${c.summary ?? ""}`}
              />
              <Link to={`/calls/${c.id}`} target="_blank">
                <span className="call-date">{c.call_date}</span>
                {c.summary && <span className="call-summary">{c.summary}</span>}
              </Link>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
