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
  asr_provider: string
  qwen3_asr_model_path: string
  qwen3_device: string
  local_llm_model_path: string
  local_llm_n_gpu_layers: number
  local_llm_n_ctx: number
  local_llm_n_batch: number
  polish_provider: string
  [key: string]: unknown
}

export interface PipelineStep {
  id: string
  name: string
  name_en?: string
}

// ---- Fetch helpers ----

const API_TOKEN_STORAGE_KEY = "mpp_api_token"

function getApiToken(): string {
  if (typeof localStorage === "undefined") return ""
  return localStorage.getItem(API_TOKEN_STORAGE_KEY) ?? ""
}

function persistApiToken(token: string) {
  if (typeof localStorage === "undefined" || typeof document === "undefined") return
  const trimmed = token.trim()
  if (trimmed) {
    localStorage.setItem(API_TOKEN_STORAGE_KEY, trimmed)
    document.cookie = `${API_TOKEN_STORAGE_KEY}=${encodeURIComponent(trimmed)}; Path=/; SameSite=Strict`
  } else {
    localStorage.removeItem(API_TOKEN_STORAGE_KEY)
    document.cookie = `${API_TOKEN_STORAGE_KEY}=; Path=/; Max-Age=0; SameSite=Strict`
  }
}

function headers(json = false): HeadersInit {
  const h: Record<string, string> = {}
  if (json) h["Content-Type"] = "application/json"
  const token = getApiToken()
  if (token) h.Authorization = `Bearer ${token}`
  return h
}

function requestedJsonHeaders(): HeadersInit {
  return { ...headers(true), "X-Requested-With": "fetch" }
}

function requestedHeaders(): HeadersInit {
  return { ...headers(false), "X-Requested-With": "fetch" }
}

async function parseError(res: Response): Promise<Error> {
  try {
    const data = await res.json()
    return new Error(data.detail || data.error || `${res.status} ${res.statusText}`)
  } catch {
    return new Error(`${res.status} ${res.statusText}`)
  }
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path, { headers: headers() })
  if (!res.ok) throw await parseError(res)
  return res.json()
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: requestedJsonHeaders(),
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) throw await parseError(res)
  return res.json()
}

async function patch<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "PATCH",
    headers: requestedJsonHeaders(),
    body: JSON.stringify(body),
  })
  if (!res.ok) throw await parseError(res)
  return res.json()
}

async function put<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "PUT",
    headers: requestedJsonHeaders(),
    body: JSON.stringify(body),
  })
  if (!res.ok) throw await parseError(res)
  return res.json()
}

async function httpDelete<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "DELETE",
    headers: requestedJsonHeaders(),
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) throw await parseError(res)
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
    patch: async (updates: Record<string, unknown>) => {
      const updated = await patch<Settings>("/api/settings", updates)
      if (typeof updates.api_token === "string" && !updates.api_token.startsWith("***...")) {
        persistApiToken(updates.api_token)
      }
      return updated
    },
  },

  archives: {
    list: () => get<{ archives: unknown[] }>("/api/pipeline/archives"),
    delete: (path: string) =>
      httpDelete<{ message: string; path: string }>("/api/pipeline/archives", { path }),
    rename: (path: string, title: string) =>
      post<{ success: boolean; title: string }>("/api/pipeline/archives/rename", { path, title }),
    thumbnailUrl: (path: string) => `/api/pipeline/archives/thumbnail?path=${encodeURIComponent(path)}`,
  },

  filesystem: {
    read: (path: string) =>
      get<{ success: boolean; content?: string; path?: string; error?: string }>(
        `/api/filesystem/read?path=${encodeURIComponent(path)}`,
      ),
    write: (path: string, content: string) =>
      post<{ success: boolean; error?: string }>("/api/filesystem/write", { path, content }),
    mediaUrl: (path: string) => `/api/filesystem/media?path=${encodeURIComponent(path)}`,
    drives: () =>
      get<{ success: boolean; drives: Array<{ name: string; path: string; is_dir: boolean; size?: number | null }> }>(
        "/api/filesystem/drives",
      ),
    browse: (path: string, mode: "file" | "directory" | "all" = "all") =>
      get<{
        success: boolean
        path: string
        error?: string
        items: Array<{ name: string; path: string; is_dir: boolean; size: number | null }>
      }>(`/api/filesystem/browse?path=${encodeURIComponent(path)}&mode=${mode}`),
    scanFolder: (path: string, recursive = true) =>
      get<{ success: boolean; files: { path: string; name: string; size: number }[]; count: number }>(
        `/api/filesystem/scan-folder?path=${encodeURIComponent(path)}&recursive=${recursive}`,
      ),
  },

  pipeline: {
    steps: () => get<{ steps: PipelineStep[] }>("/api/tasks/steps"),
    upload: async (file: File, options?: Record<string, unknown>, signal?: AbortSignal) => {
      const form = new FormData()
      form.append("file", file)
      if (options && Object.keys(options).length > 0) {
        form.append("options", JSON.stringify(options))
      }
      const res = await fetch("/api/pipeline/upload", {
        method: "POST",
        headers: requestedHeaders(),
        body: form,
        signal,
      })
      if (!res.ok) throw await parseError(res)
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

  platforms: {
    list: () =>
      get<{
        platforms: Array<{
          id: string
          name: string
          status: "active" | "coming_soon"
          auth_status: string
          preferred_quality: number | string | null
          prefer_subtitle: boolean
        }>
      }>("/api/pipeline/platforms"),
    update: (id: string, config: { preferred_quality?: number | string; prefer_subtitle?: boolean }) =>
      put<{ ok: boolean }>(`/api/pipeline/platforms/${id}`, config),
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
