/**
 * API client for MediaProcessPipeline daemon.
 * Talks to FastAPI backend at /api/* (proxied by Vite dev server).
 */

export interface Task {
  id: string
  task_type: string
  status: "pending" | "queued" | "processing" | "completed" | "failed" | "cancelled"
  source: string
  options: Record<string, unknown>
  progress: number
  message: string | null
  result: Record<string, unknown> | null
  error: string | null
  created_at: string
  updated_at: string
  completed_at: string | null
  current_step: string | null
  steps: string[]
  completed_steps: string[]
}

export interface TaskStats {
  total: number
  completed?: number
  processing?: number
  queued?: number
  failed?: number
  cancelled?: number
}

export interface Settings {
  asr_backend: string
  llm_provider: string
  [key: string]: unknown
}

// ---- Fetch helpers ----

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

async function patch<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

// ---- Tasks ----

export const api = {
  health: () => get<{ status: string }>("/health"),

  tasks: {
    create: (source: string, options: Record<string, unknown> = {}) =>
      post<Task>("/api/tasks", { task_type: "pipeline", source, options }),
    list: (status?: string, limit = 50) => {
      const params = new URLSearchParams({ limit: String(limit) })
      if (status) params.set("status", status)
      return get<Task[]>(`/api/tasks?${params}`)
    },
    get: (id: string) => get<Task>(`/api/tasks/${id}`),
    cancel: (id: string) => post<{ message: string }>(`/api/tasks/${id}/cancel`),
    stats: () => get<TaskStats>("/api/tasks/stats"),
  },

  settings: {
    get: () => get<Settings>("/api/settings"),
    patch: (updates: Record<string, unknown>) =>
      patch<Settings>("/api/settings", updates),
  },

  pipeline: {
    upload: async (file: File) => {
      const form = new FormData()
      form.append("file", file)
      const res = await fetch("/api/pipeline/upload", { method: "POST", body: form })
      if (!res.ok) throw new Error("Upload failed")
      return res.json() as Promise<{ file_path: string }>
    },
  },
}

// ---- SSE ----

export function subscribeTaskEvents(
  taskId: string,
  onEvent: (event: { task_id: string; type: string; data: Record<string, unknown>; timestamp: string }) => void,
): () => void {
  const es = new EventSource(`/api/tasks/${taskId}/events`)
  es.onmessage = (e) => {
    try { onEvent(JSON.parse(e.data)) } catch {}
  }
  return () => es.close()
}

export function subscribeAllEvents(
  onEvent: (event: { task_id: string; type: string; data: Record<string, unknown>; timestamp: string }) => void,
): () => void {
  const es = new EventSource("/api/tasks/events")
  es.onmessage = (e) => {
    try { onEvent(JSON.parse(e.data)) } catch {}
  }
  return () => es.close()
}
