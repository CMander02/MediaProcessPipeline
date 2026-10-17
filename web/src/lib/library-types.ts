import type { ArchiveItem } from "@/repositories/archive-types"

/** A content whose thumbnail stands for the folder; the chosen cover first, then the newest. */
export interface LibraryFolderPreview {
  content_key: string
  archive_id?: string | null
  path: string
  title: string
}
export interface LibraryFolder {
  id: string
  name: string
  count: number
  source_collection_key: string | null
  created_at: string
  updated_at: string
  last_processed_at?: string
  cover_content_key?: string | null
  previews?: LibraryFolderPreview[]
  /** Platform collection the folder was created from */
  collection?: { title: string; owner_name: string; avatar: "cached" | "available" | "none" }
}
export interface LibraryDirectory {
  workspace_id?: string
  folders: LibraryFolder[]
  unclassified_count: number
  library_revision: number
  members?: Record<string, string | null>
  archive_keys?: Record<string, string>
  processed_at?: Record<string, string>
}
export interface LibraryEntry { kind: "folder" | "archive"; id: string }
export interface LibraryMoveResult {
  items: Array<{ content_key: string; outcome: "moved" | "unchanged" | "skipped" | "failed"; reason?: string }>
  library_revision: number
}
export type BatchAction = "retry" | "checkpoint_rerun" | "full_rerun" | "delete" | "export" | "retention" | "move"
export type BatchScope = { type: "selected"; content_keys: string[] } | { type: "folder"; folder_id: string }
export interface BatchTarget {
  content_key: string
  folder_id: string | null
  operation_id: string
  title: string
  task_ids: string[]
  archive_ids: string[]
  records: number
  task_id: string | null
  status: string
  archive: ArchiveItem | null
}
export interface BatchPreview {
  operation_id: string
  action: BatchAction
  contents: number
  records: number
  active_records: number
  runnable: number
  targets: BatchTarget[]
  skipped_items: Array<{ content_key: string; reason: string; title?: string }>
}
export interface ImportDestination {
  folder_id?: string | null
  move_existing?: boolean
  collection_folders?: Array<{ source: string; create_folder: boolean }>
}
