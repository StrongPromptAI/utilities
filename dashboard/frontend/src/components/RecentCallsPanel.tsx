import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { Call } from "../types";
import { Panel } from "./Panel";
import { CopyButton } from "./CopyButton";

export function RecentCallsPanel({ projectId }: { projectId: number | null }) {
  const [items, setItems] = useState<Call[]>([]);

  useEffect(() => {
    api.calls(projectId ?? undefined).then(setItems).catch(() => setItems([]));
  }, [projectId]);

  return (
    <Panel title="Recent Calls">
      {items.length === 0 && <p className="muted">No calls</p>}
      <ul className="item-list">
        {items.map((c) => (
          <li key={c.id}>
            <CopyButton
              text={`[Call ${c.id}] ${c.call_date} ${c.client_name}: ${c.summary ?? "(no summary)"}`}
            />
            <Link to={`/calls/${c.id}`} target="_blank">
              <span className="call-date">{c.call_date}</span>
              <strong>{c.client_name}</strong>
              {c.project_name && <span className="tag">{c.project_name}</span>}
              {c.summary && <span className="call-summary">{c.summary}</span>}
            </Link>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
