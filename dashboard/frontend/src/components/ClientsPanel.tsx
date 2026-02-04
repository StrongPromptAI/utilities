import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { Client } from "../types";
import { Panel } from "./Panel";
import { CopyButton } from "./CopyButton";

export function ClientsPanel() {
  const [clients, setClients] = useState<Client[]>([]);

  useEffect(() => {
    api.clients().then(setClients).catch(() => setClients([]));
  }, []);

  return (
    <Panel title="Clients" defaultOpen={false}>
      {clients.length === 0 && <p className="muted">No clients</p>}
      <ul className="item-list">
        {clients.map((c) => (
          <li key={c.id}>
            <CopyButton text={`${c.name} (${c.type})${c.organization ? ` — ${c.organization}` : ""}`} />
            <Link to={`/clients/${c.name}`} target="_blank">
              <strong>{c.name}</strong>
              <span className="tag">{c.type}</span>
              {c.organization && <span className="muted"> {c.organization}</span>}
            </Link>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
