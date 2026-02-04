import type { Project } from "../types";

interface Props {
  projects: Project[];
  selected: number | null;
  onChange: (id: number | null) => void;
}

export function ProjectSelector({ projects, selected, onChange }: Props) {
  return (
    <div className="project-selector">
      <label htmlFor="project-select">Project:</label>
      <select
        id="project-select"
        value={selected ?? ""}
        onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}
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
