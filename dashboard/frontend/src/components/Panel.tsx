import { useState, type ReactNode } from "react";

interface PanelProps {
  title: string;
  defaultOpen?: boolean;
  children: ReactNode;
  actions?: ReactNode;
}

export function Panel({ title, defaultOpen = true, children, actions }: PanelProps) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <section className="bg-surface border border-border rounded-md mb-4 overflow-hidden">
      <header
        className="flex items-center gap-2 px-4 py-3 bg-surface-raised cursor-pointer select-none"
        onClick={() => setOpen(!open)}
      >
        <span className="text-xs text-muted">{open ? "\u25BC" : "\u25B6"}</span>
        <h2 className="text-base font-semibold flex-1">{title}</h2>
        {actions && <div className="flex gap-1" onClick={(e) => e.stopPropagation()}>{actions}</div>}
      </header>
      {open && <div className="p-4">{children}</div>}
    </section>
  );
}
