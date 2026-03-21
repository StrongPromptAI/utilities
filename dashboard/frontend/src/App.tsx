import { useEffect, useState } from "react";
import { api } from "./api";
import type { Project } from "./types";
import { ProjectSelector } from "./components/ProjectSelector";
import { RoadmapBoard } from "./components/RoadmapBoard";
import { ThemeSwitcher } from "./components/ThemeSwitcher";
import { DocsNav } from "./components/DocsNav";

export function App() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState<number | null>(null);

  useEffect(() => {
    api.projects().then(setProjects);
  }, []);

  return (
    <div className="max-w-[1400px] mx-auto p-4">
      <header className="flex items-center gap-8 mb-6 pb-4 border-b border-border">
        <h1 className="text-xl font-semibold text-heading">KB Dashboard</h1>
        <ProjectSelector projects={projects} selected={projectId} onChange={setProjectId} />
        <div className="ml-auto flex items-center gap-2">
          <DocsNav />
          <ThemeSwitcher />
        </div>
      </header>

      <RoadmapBoard projectId={projectId} />
    </div>
  );
}
