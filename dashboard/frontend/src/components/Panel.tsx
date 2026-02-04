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
    <section className="panel">
      <header className="panel-header" onClick={() => setOpen(!open)}>
        <span className="panel-toggle">{open ? "\u25BC" : "\u25B6"}</span>
        <h2>{title}</h2>
        {actions && <div className="panel-actions" onClick={(e) => e.stopPropagation()}>{actions}</div>}
      </header>
      {open && <div className="panel-body">{children}</div>}
    </section>
  );
}
