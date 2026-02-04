import { useState } from "react";
import { api } from "../api";
import type { SearchResult } from "../types";
import { Panel } from "./Panel";
import { CopyButton } from "./CopyButton";

export function SearchPanel() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [expanded, setExpanded] = useState<SearchResult[]>([]);
  const [showExpanded, setShowExpanded] = useState(false);
  const [loading, setLoading] = useState(false);

  const handleSearch = async () => {
    if (!query.trim()) return;
    setLoading(true);
    setExpanded([]);
    setShowExpanded(false);
    try {
      const res = await api.search(query);
      setResults(res);
    } catch {
      setResults([]);
    }
    setLoading(false);
  };

  const handleExpand = async () => {
    const ids = results.map((r) => r.id);
    if (ids.length === 0) return;
    try {
      const res = await api.searchExpand(ids);
      setExpanded(res);
      setShowExpanded(true);
    } catch {
      setExpanded([]);
    }
  };

  return (
    <Panel title="Search">
      <div className="search-bar">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleSearch()}
          placeholder="Search knowledge base..."
        />
        <button onClick={handleSearch} disabled={loading}>
          {loading ? "..." : "Search"}
        </button>
      </div>

      <div className="search-layout">
        <div className="search-results">
          {results.length === 0 && !loading && <p className="muted">No results</p>}
          <ul className="item-list">
            {results.map((r) => (
              <li key={r.id}>
                <CopyButton text={r.text} />
                <div className="search-result">
                  <div className="search-meta">
                    {r.client_name} &middot; {r.call_date}
                    {r.speaker && <> &middot; {r.speaker}</>}
                    {r.recency_score != null && (
                      <span className="score">score: {r.recency_score.toFixed(3)}</span>
                    )}
                  </div>
                  <div className="search-text">{r.text}</div>
                </div>
              </li>
            ))}
          </ul>
          {results.length > 0 && (
            <button className="expand-btn" onClick={handleExpand}>
              Cluster expand
            </button>
          )}
        </div>

        {showExpanded && (
          <div className="search-expanded">
            <h3>Related chunks ({expanded.length})</h3>
            <ul className="item-list">
              {expanded.map((r) => (
                <li key={`${r.cluster_id}-${r.id}`}>
                  <CopyButton text={r.text} />
                  <div className="search-result">
                    <div className="search-meta">
                      cluster {r.cluster_id} &middot; {r.client_name} &middot; {r.call_date}
                    </div>
                    <div className="search-text">{r.text}</div>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </Panel>
  );
}
