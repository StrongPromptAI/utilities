import { useEffect, useState } from "react";
import { api } from "./api";
import type { Project } from "./types";
import { ProjectSelector } from "./components/ProjectSelector";
import { DecisionsPanel } from "./components/DecisionsPanel";
import { TasksPanel } from "./components/TasksPanel";
import { RecentCallsPanel } from "./components/RecentCallsPanel";
import { SearchPanel } from "./components/SearchPanel";
import { ClustersPanel } from "./components/ClustersPanel";
import { ClientsPanel } from "./components/ClientsPanel";

export function App() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState<number | null>(null);

  useEffect(() => {
    api.projects().then(setProjects);
  }, []);

  return (
    <div className="dashboard">
      <header className="dashboard-header">
        <h1>KB Dashboard</h1>
        <ProjectSelector projects={projects} selected={projectId} onChange={setProjectId} />
      </header>

      <DecisionsPanel projectId={projectId} />
      <TasksPanel projectId={projectId} />
      <RecentCallsPanel projectId={projectId} />
      <SearchPanel />
      <ClustersPanel />
      <ClientsPanel />
    </div>
  );
}
