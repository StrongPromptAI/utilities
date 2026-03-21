import { useEffect, useState } from "react";
import { api } from "../api";

export function DocsNav() {
  const [docs, setDocs] = useState<{ path: string; label: string }[]>([]);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    api.docs().then(setDocs).catch(() => {});
  }, []);

  if (docs.length === 0) return null;

  return (
    <div className="relative">
      <button
        className="bg-surface text-body border border-border-input rounded px-3 py-1.5 text-sm hover:border-accent transition-colors"
        onClick={() => setOpen(!open)}
      >
        Docs
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute right-0 top-full mt-1 z-20 bg-surface border border-border rounded-md shadow-lg min-w-[200px]">
            {docs.map((d) => (
              <a
                key={d.path}
                href={`/dashboard/docs/${d.path}`}
                target="_blank"
                rel="noopener noreferrer"
                className="block px-4 py-2 text-sm text-body hover:bg-surface-raised hover:text-accent transition-colors first:rounded-t-md last:rounded-b-md"
                onClick={() => setOpen(false)}
              >
                {d.label}
              </a>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
