export interface Project {
  id: number;
  name: string;
  repo_path: string;
  created_at: string;
}

export interface OpenQuestion {
  id: number;
  project_id: number;
  topic: string;
  question: string;
  context: string | null;
  owner: string | null;
  owner_name: string | null;
  status: string;
  resolution: string | null;
  source_call_id: number | null;
  decided_by: { id: number; name: string }[] | null;
  stakeholder_type: string | null;
  created_at: string;
  updated_at: string | null;
}

/** Backward compat alias */
export type Decision = OpenQuestion;

export interface ActionItem {
  id: number;
  project_id: number;
  title: string;
  description: string | null;
  assigned_to: string | null;
  status: string;
  source_call_ids: number[] | null;
  question_id: number | null;
  question_topic: string | null;
  question_status: string | null;
  prompt_file: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface Call {
  id: number;
  call_date: string;
  source_type: string;
  summary: string | null;
  user_notes: string | null;
  client_name: string;
  project_name: string | null;
}

export interface CallDetail {
  call: Call & { client_id: number; project_id: number | null; source_file: string | null };
  participants: Participant[];
  summaries: BatchSummary[];
  chunks: Chunk[];
}

export interface Participant {
  id: number;
  call_id: number;
  name: string;
  role: string | null;
}

export interface BatchSummary {
  id: number;
  call_id: number;
  batch_idx: number;
  summary: string;
}

export interface Chunk {
  id: number;
  chunk_idx: number;
  text: string;
  speaker: string | null;
}

export interface Client {
  id: number;
  name: string;
  type: string;
  organization: string | null;
  notes: string | null;
}

export interface ClientContext {
  client: Client;
  calls: Call[];
  all_chunks_count: number;
  relevant_chunks?: SearchResult[];
}

export interface SearchResult {
  id: number;
  chunk_idx?: number;
  text: string;
  speaker: string | null;
  client_name: string;
  project_name: string | null;
  call_date: string;
  summary: string | null;
  distance?: number;
  days_old?: number;
  recency_score?: number;
  cluster_id?: number;
}

export interface ClusterDetail {
  cluster_id: number;
  label: string;
  size: number;
  chunks: {
    id: number;
    call_id: number;
    text: string;
    speaker: string | null;
    client_name: string;
    call_date: string;
  }[];
}
