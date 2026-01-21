/**
 * API client for MediaProcessPipeline backend
 */

import type { Task, TaskCreate, TaskStatus, ArchiveItem, MediaMetadata } from "@/types"

const API_BASE = import.meta.env.VITE_API_URL || "http://127.0.0.1:18000"

// API functions
async function request<T>(endpoint: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${endpoint}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
  })
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(error.detail || "Request failed")
  }
  return res.json()
}

// Tasks API
export const tasksApi = {
  create: (data: TaskCreate) =>
    request<Task>("/api/tasks", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  list: (status?: TaskStatus, limit = 50) => {
    const params = new URLSearchParams()
    if (status) params.set("status", status)
    params.set("limit", String(limit))
    return request<Task[]>(`/api/tasks?${params}`)
  },

  get: (id: string) => request<Task>(`/api/tasks/${id}`),

  cancel: (id: string) =>
    request<{ message: string; task_id: string }>(`/api/tasks/${id}/cancel`, {
      method: "POST",
    }),
}

// Pipeline API
export const pipelineApi = {
  download: (url: string) =>
    request<{ file_path: string; metadata: MediaMetadata }>("/api/pipeline/download", {
      method: "POST",
      body: JSON.stringify({ url }),
    }),

  scan: () => request<{ new_files: string[]; count: number }>("/api/pipeline/scan", { method: "POST" }),

  separate: (audioPath: string) =>
    request<{ vocals_path: string; instrumental_path: string }>(
      `/api/pipeline/separate?audio_path=${encodeURIComponent(audioPath)}`,
      { method: "POST" }
    ),

  transcribe: (audioPath: string, language?: string) =>
    request<{ segments: { start: number; end: number; text: string; speaker?: string }[]; srt: string }>(
      "/api/pipeline/transcribe",
      {
        method: "POST",
        body: JSON.stringify({ audio_path: audioPath, language }),
      }
    ),

  polish: (text: string) =>
    request<{ polished: string }>("/api/pipeline/polish", {
      method: "POST",
      body: JSON.stringify({ text }),
    }),

  summarize: (text: string) =>
    request<{ tldr: string; key_facts: string[]; action_items: string[] }>("/api/pipeline/summarize", {
      method: "POST",
      body: JSON.stringify({ text }),
    }),

  mindmap: (text: string) =>
    request<{ markdown: string }>("/api/pipeline/mindmap", {
      method: "POST",
      body: JSON.stringify({ text }),
    }),

  archives: (limit = 50) => request<{ archives: ArchiveItem[] }>(`/api/pipeline/archives?limit=${limit}`),
}

// Settings API
export const settingsApi = {
  get: () => request<Record<string, unknown>>("/api/settings"),

  update: (settings: Record<string, unknown>) =>
    request<Record<string, unknown>>("/api/settings", {
      method: "PUT",
      body: JSON.stringify(settings),
    }),

  patch: (updates: Record<string, unknown>) =>
    request<Record<string, unknown>>("/api/settings", {
      method: "PATCH",
      body: JSON.stringify(updates),
    }),
}

// Health check
export const healthCheck = () => request<{ status: string; message: string }>("/health")
