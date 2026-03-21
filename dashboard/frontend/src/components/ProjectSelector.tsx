import type { Project } from "../types";

interface Props {
  projects: Project[];
  selected: number | null;
  onChange: (id: number | null) => void;
}

export function ProjectSelector({ projects, selected, onChange }: Props) {
  return (
    <div className="flex items-center gap-2">
      <label htmlFor="project-select" className="text-muted text-sm">Project:</label>
      <select
        id="project-select"
        value={selected ?? ""}
        onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}
        className="bg-surface text-body border border-border-input rounded px-3 py-1.5 text-sm"
      >
        <option value="">All projects</option>
        {projects.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </select>
    </div>
  );
}
