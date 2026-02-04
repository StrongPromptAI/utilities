import { useEffect, useState } from "react";
import { api } from "../api";
import type { ClusterDetail } from "../types";
import { Panel } from "./Panel";
import { CopyButton } from "./CopyButton";

export function ClustersPanel() {
  const [clusters, setClusters] = useState<ClusterDetail[]>([]);

  useEffect(() => {
    api.clusters().then(setClusters).catch(() => setClusters([]));
  }, []);

  return (
    <Panel title="Clusters" defaultOpen={false}>
      {clusters.length === 0 && <p className="muted">No clusters</p>}
      <ul className="item-list">
        {clusters.map((cl) => (
          <li key={cl.cluster_id} className="cluster-item">
            <CopyButton
              text={`${cl.label} (${cl.size} chunks): ${cl.chunks[0]?.text.slice(0, 120) ?? ""}`}
            />
            <div>
              <strong>{cl.label}</strong> <span className="muted">({cl.size} chunks)</span>
              <ul className="chunk-list">
                {cl.chunks.slice(0, 3).map((ch) => (
                  <li key={ch.id}>
                    <span className="search-meta">
                      {ch.client_name} &middot; {ch.call_date}
                    </span>
                    <span className="search-text">{ch.text.slice(0, 200)}</span>
                  </li>
                ))}
                {cl.chunks.length > 3 && (
                  <li className="muted">...{cl.chunks.length - 3} more</li>
                )}
              </ul>
            </div>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
