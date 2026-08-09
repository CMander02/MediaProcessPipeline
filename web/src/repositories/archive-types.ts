export interface OfflineFileDescriptor {
  relativePath: string
  url: string
  size: number
  mime: string
}

export interface ArchiveItem {
  archive_id?: string
  revision?: number
  title: string
  date: string
  created_at: string
  path: string
  server_path?: string
  has_transcript: boolean
  has_summary: boolean
  has_mindmap: boolean
  has_video: boolean
  has_audio: boolean
  has_image: boolean
  media_file: string | null
  processing?: boolean
  task_id?: string
  metadata: Record<string, unknown>
  duration_seconds: number | null
  analysis: {
    language?: string
    content_type?: string
    main_topics?: string[]
    keywords?: string[]
    proper_nouns?: string[]
    speakers_detected?: number
    tone?: string
  }
  offline?: boolean
  offlineFiles?: OfflineFileDescriptor[]
  thumbnail_url?: string
}
