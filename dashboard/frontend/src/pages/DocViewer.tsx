import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import Markdown from "react-markdown";
import { api } from "../api";

export function DocViewer() {
  const { "*": docPath } = useParams();
  const [content, setContent] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    if (!docPath) return;
    api.doc(docPath)
      .then((r) => { setContent(r.content); setDraft(r.content); })
      .catch(() => setError("Document not found"));
  }, [docPath]);

  function save() {
    if (!docPath) return;
    setSaveError(null);
    api.saveDoc(docPath, draft).then((r) => {
      if (r.ok) {
        setContent(draft);
        setEditing(false);
      } else {
        setSaveError("Storage backend not configured — deploy to Railway to enable editing");
      }
    }).catch(() => {
      setSaveError("Storage backend not configured — deploy to Railway to enable editing");
    });
  }

  if (error) {
    return (
      <div className="max-w-4xl mx-auto p-8">
        <Link to="/" className="text-accent text-sm mb-4 inline-block">&larr; Back</Link>
        <p className="text-status-red">{error}</p>
      </div>
    );
  }

  if (content === null) {
    return (
      <div className="max-w-4xl mx-auto p-8">
        <p className="text-muted">Loading...</p>
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto p-8">
      <div className="flex items-center gap-4 mb-6">
        <Link to="/" className="text-accent text-sm">&larr; Dashboard</Link>
        <span className="text-faint text-sm flex-1 font-mono">{docPath}</span>
        <button
          className={`rounded px-4 py-1.5 text-sm font-medium ${
            editing
              ? "bg-status-yellow-bg text-status-yellow"
              : "bg-btn text-white hover:bg-btn-hover"
          }`}
          onClick={() => {
            if (editing) {
              setDraft(content);
              setEditing(false);
              setSaveError(null);
            } else {
              setEditing(true);
            }
          }}
        >
          {editing ? "Cancel" : "Edit"}
        </button>
        {editing && (
          <button
            className="bg-status-green text-white rounded px-4 py-1.5 text-sm font-medium"
            onClick={save}
          >
            Save
          </button>
        )}
      </div>

      {saveError && (
        <div className="bg-status-orange-bg text-status-orange rounded px-4 py-2 text-sm mb-4">
          {saveError}
        </div>
      )}

      {editing ? (
        <textarea
          className="w-full min-h-[600px] bg-base text-body border border-border-input rounded p-4 font-mono text-sm leading-relaxed resize-y"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
        />
      ) : (
        <article className="prose-kb">
          <Markdown>{content}</Markdown>
        </article>
      )}
    </div>
  );
}
