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
  has_thumbnail?: boolean
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
  /** Number of runs of the same source collapsed into this card (2+ only). */
  attempts?: number
}
import type { ArchiveSort, ArchiveStatus, MediaFilter } from "@/lib/archive-filters"

export interface ArchiveQuery {
  page: number
  page_size: number
  search: string
  /** "all" or a comma list of video, audio, image */
  media: string
  /** "all" or a comma list of source keys */
  source: string
  sort: ArchiveSort
  /** "all" or a comma list of processing, paused, failed, completed */
  status?: string
  /** List every run of sources processed more than once */
  duplicates?: boolean
}

export interface ArchiveIndexStatus {
  workspace_id: string
  revision: number
  indexing: boolean
  last_reconciled_at: string | null
}

export interface ArchiveFacets {
  /** Per status, plus "all" and how many cards the duplicates switch would list */
  status: Record<ArchiveStatus | "all" | "duplicates", number>
  source: Partial<Record<string, number>>
  media: Record<MediaFilter, number>
  /** Every card in the library, ignoring all filters */
  total?: number
}

export interface ArchivePage extends ArchiveIndexStatus {
  archives: ArchiveItem[]
  total: number
  page: number
  page_size: number
  /** Counts for status chips and filter menus (server index only). */
  facets?: ArchiveFacets
}
