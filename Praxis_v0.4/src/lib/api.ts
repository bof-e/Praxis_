// Praxis frontend - thin API client for the FastAPI backend.
// Every call to the backend goes through here so the base URL and error
// handling live in exactly one place.

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers:
      init?.body && !(init.body instanceof FormData)
        ? { "Content-Type": "application/json", ...(init?.headers || {}) }
        : init?.headers,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {
      // ignore - non-JSON error body
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

// ---- Types (mirrors the FastAPI response shapes we actually use) --------

export const TASK_TYPES = [
  "analyse_donnees",
  "reponse_ao",
  "rapport_evaluation",
  "planification",
  "recherche",
  "redaction",
  "autre",
] as const;
export type TaskType = (typeof TASK_TYPES)[number];

export const TASK_TYPE_LABELS: Record<TaskType, string> = {
  analyse_donnees: "Analyse de données",
  reponse_ao: "Réponse à appel d'offres",
  rapport_evaluation: "Rapport d'évaluation",
  planification: "Planification",
  recherche: "Recherche",
  redaction: "Rédaction",
  autre: "Autre",
};

export const STATUS_LABELS: Record<string, string> = {
  draft: "Brouillon",
  understanding: "Compréhension",
  clarification: "Clarification",
  contextualization: "Contextualisation",
  planning: "Planification",
  plan_validation: "Plan à valider",
  tool_selection: "Sélection des outils",
  execution: "Exécution",
  error_recovery: "Erreur — action requise",
  validation: "Validation",
  deliverable: "Livrable prêt",
  final_validation: "Validation finale",
  deployed: "Livré",
  learning: "Apprentissage",
  abandoned: "Abandonnée",
};

export interface Task {
  id: string;
  title: string;
  raw_request: string;
  objective?: string | null;
  type: TaskType;
  domain?: string | null;
  status: string;
  readiness_score?: number | null;
  autonomy_level?: string | null;
  project_id?: string | null;
  created_at: string;
}

export interface PlanStep {
  type: string;
  name: string;
  is_critical?: boolean;
  strategy?: string;
}

export interface Plan {
  id: string;
  task_id: string;
  version: number;
  status: string;
  steps: PlanStep[];
  checkpoints: number[];
}

export interface ArtifactSummary {
  id: string;
  kind: string;
  format: string;
  version: number;
  produced_by_agent?: string | null;
  created_at: string;
}

export interface DeliverableSummary {
  id: string;
  artifact_id: string;
  validated: boolean;
}

export interface ExecutionSummary {
  id: string;
  step_index: number;
  agent_assigned: string;
  status: string;
  logs: string[];
}

export interface TaskDetail {
  task: Task & { data_sources: { path: string; original_name: string }[] };
  plan: Plan | null;
  artifacts: ArtifactSummary[];
  deliverables: DeliverableSummary[];
  executions: ExecutionSummary[];
}

export interface ExecuteResult {
  task_id: string;
  final_status?: string;
  requires_user_action: boolean;
  user_action: string | null;
  resumed_from_step?: number | null;
  steps_run: Record<string, unknown>[];
  artifacts: { id: string; role: string; kind: string; format: string }[];
  deliverables: { id: string; artifact_id: string; role: string }[];
  validation: { verdict?: string; score?: number } | null;
}

export interface TraceabilityAnswer {
  found: boolean;
  row_id: string;
  message?: string;
  explanation?: string;
  matches?: { variable: string; original_value: unknown; reason: string; action: string }[];
}

// ---- Tasks ---------------------------------------------------------------

export const api = {
  listTasks: (status?: string) =>
    request<Task[]>(`/tasks${status ? `?status=${status}` : ""}`),

  getTask: (id: string) => request<Task>(`/tasks/${id}`),

  getTaskDetail: (id: string) => request<TaskDetail>(`/tasks/${id}/detail`),

  getReadinessModel: (id: string) =>
    request<{
      dimensions: string[];
      critical_dimensions: string[];
      threshold_global: number;
      threshold_critical: number;
      labels: Record<string, string>;
    }>(`/tasks/${id}/readiness-model`),

  createTask: (data: {
    title: string;
    raw_request: string;
    task_type: TaskType;
    objective?: string;
    domain?: string;
  }) => request<Task>("/tasks", { method: "POST", body: JSON.stringify(data) }),

  deleteTask: (id: string) => request<void>(`/tasks/${id}`, { method: "DELETE" }),

  uploadData: (taskId: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<{ data_sources: unknown[] }>(`/tasks/${taskId}/data`, {
      method: "POST",
      body: form,
    });
  },

  updateReadiness: (taskId: string, dimensionScores: Record<string, number>) =>
    request<{
      readiness_score: number;
      is_ready: boolean;
      missing_critical_dimensions: string[];
      clarification_questions: string[];
      new_status: string;
    }>(`/tasks/${taskId}/readiness`, {
      method: "POST",
      body: JSON.stringify({ dimension_scores: dimensionScores }),
    }),

  proposePlan: (taskId: string) =>
    request<{ plan: Plan; missing_info?: string[]; generation_method?: "llm" | "heuristic" | "poles" }>(
      `/tasks/${taskId}/plan`,
      { method: "POST" }
    ),

  routeDomain: (taskId: string) =>
    request<{
      poles: string[];
      type_livrable: string;
      confiance: number;
      needs_clarification: boolean;
      source: "llm" | "deterministic_fallback";
    }>(`/tasks/${taskId}/route-domain`, { method: "POST" }),

  listExpertiseDomains: () =>
    request<{ code: string; nom: string; capacites: string[]; librairies: string[] }[]>(
      "/expertise-domains"
    ),

  reformulate: (taskId: string) =>
    request<{
      available: boolean;
      objective?: string;
      dimension_scores?: Record<string, number>;
      rationale?: string;
    }>(`/tasks/${taskId}/reformulate`, { method: "POST" }),

  getDefaultDeliverables: (taskId: string) =>
    request<{
      choices: { key: string; label: string }[];
      defaults: string[];
      current_selection: string[] | null;
    }>(`/tasks/${taskId}/default-deliverables`),

  setDeliverables: (taskId: string, deliverables: string[]) =>
    request<{ task_id: string; desired_deliverables: string[] }>(
      `/tasks/${taskId}/deliverables`,
      { method: "POST", body: JSON.stringify({ deliverables }) }
    ),

  validatePlan: (planId: string, approved: boolean) =>
    request<{ plan: Plan; status: string }>(`/plans/${planId}/validate`, {
      method: "POST",
      body: JSON.stringify({ approved }),
    }),

  executeTask: (taskId: string, force = false) =>
    request<ExecuteResult>(`/tasks/${taskId}/execute${force ? "?force=true" : ""}`, {
      method: "POST",
    }),

  executeTaskAsync: (taskId: string, force = false) =>
    request<{ started: boolean; task_id: string }>(
      `/tasks/${taskId}/execute-async${force ? "?force=true" : ""}`,
      { method: "POST" }
    ),

  getExecutionStatus: (taskId: string) =>
    request<{ task_id: string; status: string; running: boolean; started_at: string | null }>(
      `/tasks/${taskId}/execution-status`
    ),

  listExecutions: (taskId: string) =>
    request<ExecutionSummary[]>(`/tasks/${taskId}/executions`),

  explainRow: (taskId: string, rowId: string) =>
    request<TraceabilityAnswer>(
      `/tasks/${taskId}/traceability?row_id=${encodeURIComponent(rowId)}`
    ),

  downloadUrl: (artifactId: string) => `${API_BASE}/artifacts/${artifactId}/download`,

  validateDeliverable: (deliverableId: string) =>
    request<DeliverableSummary>(`/deliverables/${deliverableId}/validate`, {
      method: "POST",
    }),
};

// ---- Knowledge Base -------------------------------------------------------

export const KB_CONTENT_TYPES = ["method", "norm", "template", "reference", "course", "article"] as const;
export type KBContentType = (typeof KB_CONTENT_TYPES)[number];

export const KB_CONTENT_TYPE_LABELS: Record<KBContentType, string> = {
  method: "Méthode",
  norm: "Norme",
  template: "Gabarit",
  reference: "Référence",
  course: "Cours",
  article: "Article",
};

export interface KnowledgeItem {
  id: string;
  title: string;
  domain: string;
  content_type: KBContentType;
  summary?: string | null;
  confidence?: string | null;
  tags?: string[] | null;
  created_at: string;
}

export interface KnowledgeSearchHit extends KnowledgeItem {
  content: string;
  source_ref?: string | null;
  score: number;
}

export interface KnowledgeAnswer {
  query: string;
  sources: KnowledgeSearchHit[];
  synthesis: string | null;
}

export const knowledgeApi = {
  list: (domain?: string) =>
    request<KnowledgeItem[]>(`/knowledge${domain ? `?domain=${encodeURIComponent(domain)}` : ""}`),

  search: (q: string, domain?: string) =>
    request<KnowledgeSearchHit[]>(
      `/knowledge?q=${encodeURIComponent(q)}${domain ? `&domain=${encodeURIComponent(domain)}` : ""}`
    ),

  add: (item: {
    title: string;
    domain: string;
    content: string;
    content_type: KBContentType;
    source_ref?: string;
    tags?: string[];
  }) => request<KnowledgeItem>("/knowledge", { method: "POST", body: JSON.stringify(item) }),

  remove: (id: string) => request<{ deleted: boolean }>(`/knowledge/${id}`, { method: "DELETE" }),

  ask: (query: string, domain?: string) =>
    request<KnowledgeAnswer>("/knowledge/ask", {
      method: "POST",
      body: JSON.stringify({ query, domain }),
    }),
};
