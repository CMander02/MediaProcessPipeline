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
  llm_provider: string
  qwen3_asr_model_path: string
  qwen3_device: string
  local_llm_model_path: string
  local_llm_n_gpu_layers: number
  local_llm_n_ctx: number
  local_llm_n_batch: number
  polish_provider: string
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
    headers: { "Content-Type": "application/json", "X-Requested-With": "fetch" },
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

async function patch<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", "X-Requested-With": "fetch" },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

async function httpDelete<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "DELETE",
    headers: { "Content-Type": "application/json", "X-Requested-With": "fetch" },
    body: body ? JSON.stringify(body) : undefined,
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
    delete: (id: string) => httpDelete<{ message: string }>(`/api/tasks/${id}`),
    stats: () => get<TaskStats>("/api/tasks/stats"),
  },

  settings: {
    get: () => get<Settings>("/api/settings"),
    patch: (updates: Record<string, unknown>) =>
      patch<Settings>("/api/settings", updates),
  },

  archives: {
    delete: (path: string) =>
      httpDelete<{ message: string; path: string }>("/api/pipeline/archives", { path }),
    rename: (path: string, title: string) =>
      post<{ success: boolean; title: string }>("/api/pipeline/archives/rename", { path, title }),
  },

  filesystem: {
    write: (path: string, content: string) =>
      post<{ success: boolean; error?: string }>("/api/filesystem/write", { path, content }),
    scanFolder: (path: string, recursive = true) =>
      get<{ success: boolean; files: { path: string; name: string; size: number }[]; count: number }>(
        `/api/filesystem/scan-folder?path=${encodeURIComponent(path)}&recursive=${recursive}`,
      ),
  },

  pipeline: {
    upload: async (file: File, options?: Record<string, unknown>, signal?: AbortSignal) => {
      const form = new FormData()
      form.append("file", file)
      if (options && Object.keys(options).length > 0) {
        form.append("options", JSON.stringify(options))
      }
      const res = await fetch("/api/pipeline/upload", {
        method: "POST",
        headers: { "X-Requested-With": "fetch" },
        body: form,
        signal,
      })
      if (!res.ok) throw new Error("Upload failed")
      return res.json() as Promise<Task>
    },
    probe: (url: string) =>
      get<{ title?: string; description?: string; tags?: string[]; uploader?: string; duration?: number }>(
        `/api/pipeline/probe?url=${encodeURIComponent(url)}`,
      ),
  },

  voiceprints: {
    listPersons: () =>
      get<Array<{ id: string; name: string; notes: string; created_at: string; sample_count: number }>>(
        "/api/voiceprints/persons",
      ),
    patchPerson: (id: string, body: { name?: string; notes?: string }) =>
      patch<{ id: string; name: string; notes: string; created_at: string; sample_count: number }>(
        `/api/voiceprints/persons/${id}`,
        body,
      ),
    deletePerson: (id: string) =>
      httpDelete<{ success: boolean }>(`/api/voiceprints/persons/${id}`),
    mergePersons: (dstId: string, srcId: string) =>
      post<{ id: string; name: string; notes: string; created_at: string; sample_count: number }>(
        `/api/voiceprints/persons/${dstId}/merge`,
        { src_person_id: srcId },
      ),
    sampleClipUrl: (sampleId: string) => `/api/voiceprints/samples/${sampleId}/clip`,
    renameTaskSpeaker: (
      taskId: string,
      oldName: string,
      newName: string,
      onConflict: "ask" | "merge" | "new" = "ask",
    ) =>
      patch<{
        status: "renamed" | "merged" | "conflict"
        person_id?: string
        person_name?: string
        conflict_person_id?: string
        conflict_person_name?: string
        conflict_sample_count?: number
      }>(`/api/tasks/${taskId}/speakers`, { old_name: oldName, new_name: newName, on_conflict: onConflict }),
  },

  bilibili: {
    status: () =>
      get<{ logged_in: boolean; uid?: string; expires?: string; days_left?: number; message?: string }>(
        "/api/pipeline/bilibili/status",
      ),
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
