import type {
  Project,
  Decision,
  OpenQuestion,
  ActionItem,
  Call,
  CallDetail,
  Client,
  ClientContext,
  SearchResult,
  ClusterDetail,
} from "./types";

async function get<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export const api = {
  projects: () => get<Project[]>("/api/projects"),

  decisions: (projectId: number, status?: string) => {
    const params = status ? `?status=${status}` : "";
    return get<Decision[]>(`/api/projects/${projectId}/decisions${params}`);
  },
  decision: (id: number) => get<Decision>(`/api/decisions/${id}`),

  questions: (projectId: number, status?: string) => {
    const params = status ? `?status=${status}` : "";
    return get<OpenQuestion[]>(`/api/projects/${projectId}/questions${params}`);
  },
  question: (id: number) => get<OpenQuestion>(`/api/questions/${id}`),

  actions: (projectId: number, status?: string) => {
    const params = status ? `?status=${status}` : "";
    return get<ActionItem[]>(`/api/projects/${projectId}/actions${params}`);
  },
  action: (id: number) => get<ActionItem>(`/api/actions/${id}`),
  actionPrompt: async (id: number): Promise<string> => {
    const res = await fetch(`/api/actions/${id}/prompt`);
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.text();
  },

  calls: (projectId?: number, limit = 20) => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (projectId) params.set("project_id", String(projectId));
    return get<Call[]>(`/api/calls?${params}`);
  },
  call: (id: number) => get<CallDetail>(`/api/calls/${id}`),

  clients: () => get<Client[]>("/api/clients"),
  client: (name: string) => get<ClientContext>(`/api/clients/${name}`),

  search: (q: string, client?: string, limit = 10) => {
    const params = new URLSearchParams({ q, limit: String(limit) });
    if (client) params.set("client", client);
    return get<SearchResult[]>(`/api/search?${params}`);
  },
  searchExpand: (chunkIds: number[]) =>
    get<SearchResult[]>(`/api/search/expand?chunk_ids=${chunkIds.join(",")}`),

  clusters: (callId?: number, minSize = 2) => {
    const params = new URLSearchParams({ min_size: String(minSize) });
    if (callId) params.set("call_id", String(callId));
    return get<ClusterDetail[]>(`/api/clusters?${params}`);
  },
};
