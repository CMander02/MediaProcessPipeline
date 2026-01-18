// Task types
export type TaskStatus = "pending" | "queued" | "processing" | "completed" | "failed" | "cancelled"
export type TaskType = "ingestion" | "preprocessing" | "recognition" | "analysis" | "archiving" | "pipeline"
export type MediaType = "video" | "audio" | "podcast" | "meeting" | "other"

export interface MediaMetadata {
  title: string
  source_url?: string
  uploader?: string
  upload_date?: string
  duration_seconds?: number
  media_type: MediaType
  file_path?: string
  file_hash?: string
  extra?: Record<string, unknown>
}

export interface Task {
  id: string
  task_type: TaskType
  status: TaskStatus
  source: string
  options: Record<string, unknown>
  progress: number
  message?: string
  result?: Record<string, unknown>
  error?: string
  webhook_url?: string
  created_at: string
  updated_at: string
  completed_at?: string
}

export interface TaskCreate {
  task_type: TaskType
  source: string
  options?: Record<string, unknown>
  webhook_url?: string
}

export interface ArchiveItem {
  title: string
  date: string
  path: string
  has_transcript: boolean
  has_summary: boolean
  has_mindmap: boolean
}

// Settings types
export interface Settings {
  // API
  anthropic_api_key: string
  openai_api_key: string
  llm_provider: "anthropic" | "openai"
  llm_model: string
  // WhisperX
  whisper_model: string
  whisper_device: "cpu" | "cuda"
  whisper_compute_type: string
  enable_diarization: boolean
  hf_token: string
  // Paths
  inbox_path: string
  processing_path: string
  outputs_path: string
  archive_path: string
  obsidian_vault_path: string
  // UVR
  uvr_model: string
  uvr_device: "cpu" | "cuda"
}

// Pipeline options
export interface PipelineOptions {
  skip_download: boolean
  skip_separation: boolean
  skip_diarization: boolean
  language: string
}

// Navigation
export type PageName = "home" | "tasks" | "archives" | "settings"

export interface NavItem {
  id: PageName
  label: string
  icon: string
}
