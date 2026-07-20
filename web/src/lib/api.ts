/**
 * API client for MediaProcessPipeline daemon.
 * Talks to FastAPI backend at /api/* (proxied by Vite dev server).
 */

import { createSettingsPatch, type SettingsPatchInput } from "./settings-patch"
import type { ProviderConfig, ProviderModelRecord, RuntimeSettings } from "./settings-schema"

export interface Task {
  id: string
  task_type: string
  status: "pending" | "queued" | "processing" | "paused" | "completed" | "failed" | "cancelled"
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
  flow?: TaskFlowSnapshot | null
  content_subtype?: string | null
  platform?: string | null
}

export interface TaskFlowStep {
  id: string
  label: string
}

export interface TaskFlowSnapshot {
  id: string
  label: string
  platform: string
  branch?: string
  content_subtype?: string
  current_step: string | null
  current_step_index: number
  current_step_label?: string
  total_steps: number
  progress: number
  status: string
  steps: TaskFlowStep[]
  completed_steps?: string[]
}

export interface TaskTimelineEvent {
  id: number
  task_id: string
  event_type: string
  stage?: string | null
  step_id?: string | null
  level: "debug" | "info" | "warning" | "error" | string
  message?: string | null
  data: Record<string, unknown>
  timestamp: string
}

export interface TaskStats {
  total: number
  completed?: number
  processing?: number
  queued?: number
  failed?: number
  cancelled?: number
  paused?: number
}

export interface BilibiliCollectionItem {
  id: string
  bvid: string
  page: number
  title: string
  duration: number | null
  cover: string | null
  section?: string | null
  url: string
}

export interface BilibiliCollectionResult {
  is_bilibili: boolean
  is_collection: boolean
  collection_type?: "multipart" | "ugc_season"
  title?: string
  current_item_id?: string
  items: BilibiliCollectionItem[]
}

export interface XiaohongshuAuthStatus {
  configured_cookie: boolean
  storage_state_path: string
  storage_state_exists: boolean
  cookie_count: number
  login_cookie: boolean
  updated_at?: string
  auth_status?: string
  error?: string
}

export interface Settings extends RuntimeSettings {
  llm_provider: string
  asr_provider: string
  audio_processing_flow: string
  qwen3_asr_model_path: string
  qwen3_device: string
  llama_cpp_binary_path: string
  qwen3_gguf_model_path: string
  qwen3_gguf_mmproj_path: string
  qwen3_gguf_hf_repo: string
  qwen3_gguf_device: string
  qwen3_gguf_ctx: number
  qwen3_gguf_n_gpu_layers: number
  qwen3_gguf_timeout_sec: number
  qwen3_gguf_keepalive_sec: number
  qwen3_gguf_chunk_strategy: string
  silero_onnx_model_path: string
  moss_cpp_binary_path: string
  moss_cpp_model_path: string
  moss_cpp_device: string
  moss_cpp_threads: number
  moss_cpp_max_new_tokens: number
  moss_cpp_timeout_sec: number
  local_llm_model_path: string
  local_llm_engine: string
  local_llm_name: string
  local_llm_mmproj_path: string
  local_llm_device: string
  local_llm_dtype: string
  local_llm_max_new_tokens: number
  local_llm_n_gpu_layers: number
  local_llm_n_ctx: number
  local_llm_n_batch: number
  local_llm_timeout_sec: number
  local_llm_keepalive_sec: number
  local_llm_concurrency: number
  polish_provider: string
  [key: string]: unknown
}

export interface ProviderModelListItem {
  id: string
  display_name?: string
  model_type?: string
}

export interface ProviderModelSyncResult {
  provider: ProviderConfig
  models: ProviderModelRecord[]
}

export interface ProviderModelCatalogResult {
  provider_id: string
  source: "remote" | "configured"
  models: ProviderModelRecord[]
  configured_models: ProviderModelRecord[]
  allowed_models: ProviderModelRecord[]
  error?: string | null
}

export interface TwitterAuthStatus {
  storage_state_path: string
  storage_state_exists: boolean
  cookie_count: number
  logged_in: boolean
  updated_at?: string
  error?: string
}

export interface YtdlpStatus {
  installed: string | null
  latest: string | null
  age_days: number | null
  is_stale: boolean
  auto_update: boolean
}

export interface YtdlpUpgradeResult {
  ok: boolean
  old: string | null
  new: string | null
  output: string
  command?: string[]
  restart_recommended?: boolean
  restart_scheduled?: boolean
}

export interface PipelineStep {
  id: string
  name: string
  name_en?: string
}

// ---- Fetch helpers ----

export const API_TOKEN_STORAGE_KEY = "mpp_api_token"

export function getApiToken(): string {
  if (typeof localStorage === "undefined") return ""
  return localStorage.getItem(API_TOKEN_STORAGE_KEY) ?? ""
}

export function persistApiToken(token: string) {
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
    const data = await readJson<Record<string, unknown>>(res)
    const detail =
      typeof data.detail === "string"
        ? data.detail
        : typeof data.error === "string"
          ? data.error
          : `${res.status} ${res.statusText}`
    return new Error(detail)
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error)
    return new Error(`${res.status} ${res.statusText}: ${detail}`)
  }
}

async function readJson<T>(res: Response): Promise<T> {
  const text = await res.text()
  if (!text.trim()) return undefined as T
  try {
    return JSON.parse(text) as T
  } catch (error) {
    const snippet = text.trim().slice(0, 80).replace(/\s+/g, " ")
    if (snippet.toLowerCase().startsWith("<!doctype") || snippet.toLowerCase().startsWith("<html")) {
      throw new Error("接口返回了前端页面，请重启后端并确认 API 路由已加载。")
    }
    throw new Error(`接口返回内容不是 JSON：${snippet || String(error)}`)
  }
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path, { headers: headers() })
  if (!res.ok) throw await parseError(res)
  return readJson<T>(res)
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: requestedJsonHeaders(),
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) throw await parseError(res)
  return readJson<T>(res)
}

async function patch<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "PATCH",
    headers: requestedJsonHeaders(),
    body: JSON.stringify(body),
  })
  if (!res.ok) throw await parseError(res)
  return readJson<T>(res)
}

async function put<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "PUT",
    headers: requestedJsonHeaders(),
    body: JSON.stringify(body),
  })
  if (!res.ok) throw await parseError(res)
  return readJson<T>(res)
}

async function httpDelete<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "DELETE",
    headers: requestedJsonHeaders(),
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) throw await parseError(res)
  return readJson<T>(res)
}

// ---- Tasks ----

export const api = {
  health: () => get<{ status: string }>("/health"),

  tasks: {
    create: (source: string, options: Record<string, unknown> = {}) =>
      post<Task>("/api/tasks", { task_type: "pipeline", source, options }),
    createBatch: (sources: string[], options: Record<string, unknown> = {}) =>
      post<Task[]>("/api/tasks/batch", { task_type: "pipeline", sources, options }),
    list: (status?: string, limit = 50) => {
      const params = new URLSearchParams({ limit: String(limit) })
      if (status) params.set("status", status)
      return get<Task[]>(`/api/tasks?${params}`)
    },
    get: (id: string) => get<Task>(`/api/tasks/${id}`),
    timeline: (id: string, limit = 1000) =>
      get<{ task_id: string; events: TaskTimelineEvent[] }>(`/api/tasks/${id}/timeline?limit=${limit}`),
    cancel: (id: string) => post<{ message: string }>(`/api/tasks/${id}/cancel`),
    pause: (id: string) => post<{ message: string }>(`/api/tasks/${id}/pause`),
    resume: (id: string) => post<{ message: string }>(`/api/tasks/${id}/resume`),
    checkpointRerun: (id: string) => post<{ message: string }>(`/api/tasks/${id}/checkpoint-rerun`),
    delete: (id: string) => httpDelete<{ message: string; deleted_paths?: string[]; errors?: Array<Record<string, string>> }>(`/api/tasks/${id}`),
    stats: () => get<TaskStats>("/api/tasks/stats"),
  },

  settings: {
    get: () => get<Settings>("/api/settings"),
    patch: async (updates: SettingsPatchInput) => {
      const preparedUpdates = createSettingsPatch(updates)
      const updated = await patch<Settings>("/api/settings", preparedUpdates)
      if (typeof preparedUpdates.api_token === "string") {
        persistApiToken(preparedUpdates.api_token)
      }
      return updated
    },
    detectLocalUvr: () => get<{ found: boolean; path: string; models: string[] }>("/api/settings/uvr/local"),
    fetchSiliconFlowModels: () =>
      get<{ models: ProviderModelListItem[] }>("/api/settings/providers/siliconflow/models"),
    fetchProviderModels: (providerId: string, capability?: string) => {
      const params = new URLSearchParams()
      if (capability) params.set("capability", capability)
      const suffix = params.toString() ? `?${params}` : ""
      return get<ProviderModelCatalogResult>(`/api/settings/providers/${providerId}/models/catalog${suffix}`)
    },
    syncProviderModels: (providerId: string) =>
      post<ProviderModelSyncResult>(`/api/settings/providers/${providerId}/models/sync`),
    inferProviderModelMetadata: (body: { model_id: string; model_type?: string; display_name?: string; provider_id?: string }) =>
      post<ProviderModelRecord>("/api/settings/providers/models/metadata", body),
    queryProviderBalance: (providerId: string) =>
      post<{ provider_id: string; balance: unknown }>(`/api/settings/providers/${providerId}/balance`),
    ytdlpStatus: () => get<YtdlpStatus>("/api/settings/ytdlp"),
    upgradeYtdlp: () => post<YtdlpUpgradeResult>("/api/settings/ytdlp/upgrade"),
  },

  archives: {
    list: (options: { lite?: boolean } = {}) => {
      const params = new URLSearchParams()
      if (options.lite) params.set("lite", "true")
      const suffix = params.toString() ? `?${params}` : ""
      return get<{ archives: unknown[] }>(`/api/pipeline/archives${suffix}`)
    },
    get: (path: string) =>
      get<{ archive: unknown }>(`/api/pipeline/archives/detail?path=${encodeURIComponent(path)}`),
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
    openFolder: (path: string) =>
      post<{ success: boolean; path: string }>("/api/filesystem/open-folder", { path }),
    scanFolder: (path: string, recursive = true) =>
      get<{ success: boolean; files: { path: string; name: string; size: number }[]; count: number }>(
        `/api/filesystem/scan-folder?path=${encodeURIComponent(path)}&recursive=${recursive}`,
      ),
  },

  pipeline: {
    steps: () => get<{ steps: PipelineStep[] }>("/api/tasks/steps"),
    stage: async (file: File, signal?: AbortSignal) => {
      const form = new FormData()
      form.append("file", file)
      const res = await fetch("/api/pipeline/stage", {
        method: "POST",
        headers: requestedHeaders(),
        body: form,
        signal,
      })
      if (!res.ok) throw await parseError(res)
      return res.json() as Promise<{
        staging_id: string
        path: string
        filename: string
        title: string
        size: number
        media_type: string
      }>
    },
    deleteStaged: (stagingId: string) =>
      httpDelete<{ deleted: boolean }>(`/api/pipeline/stage/${stagingId}`),
    probe: (url: string) =>
      get<{ title?: string; description?: string; tags?: string[]; uploader?: string; duration?: number }>(
        `/api/pipeline/probe?url=${encodeURIComponent(url)}`,
      ),
    bilibiliCollection: (url: string) =>
      get<BilibiliCollectionResult>(
        `/api/pipeline/bilibili/collection?url=${encodeURIComponent(url)}`,
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

  xiaohongshu: {
    status: () =>
      get<XiaohongshuAuthStatus>("/api/pipeline/xiaohongshu/auth/status"),
    login: (timeoutSec = 180) =>
      post<XiaohongshuAuthStatus>("/api/pipeline/xiaohongshu/auth/login", { timeout_sec: timeoutSec }),
  },

  twitter: {
    status: () => get<TwitterAuthStatus>("/api/pipeline/twitter/auth/status"),
    login: (timeoutSec = 180) =>
      post<TwitterAuthStatus>("/api/pipeline/twitter/auth/login", { timeout_sec: timeoutSec }),
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
          subtitle_engine?: string
          subtitle_languages?: string
          subtitle_strict_validation?: boolean
          subtitle_min_coverage?: number
          subtitle_allow_legacy_fallback?: boolean
          image_strategy_order?: string[]
          fail_on_missing_images?: boolean
          storage_state_path?: string
          storage_state_exists?: boolean
          login_cookie?: boolean
        }>
      }>("/api/pipeline/platforms"),
    update: (id: string, config: {
      preferred_quality?: number | string
      prefer_subtitle?: boolean
      subtitle_engine?: string
      subtitle_languages?: string
      subtitle_strict_validation?: boolean
      subtitle_min_coverage?: number
      subtitle_allow_legacy_fallback?: boolean
      image_strategy_order?: string[]
      fail_on_missing_images?: boolean
    }) =>
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
    try {
      onEvent(JSON.parse(e.data))
    } catch {
      // Ignore malformed SSE payloads from interrupted connections.
    }
  }
  return () => es.close()
}

export function subscribeAllEvents(
  onEvent: (event: { task_id: string; type: string; data: Record<string, unknown>; timestamp: string }) => void,
): () => void {
  const es = new EventSource("/api/tasks/events")
  es.onmessage = (e) => {
    try {
      onEvent(JSON.parse(e.data))
    } catch {
      // Ignore malformed SSE payloads from interrupted connections.
    }
  }
  return () => es.close()
}
