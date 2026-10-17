import { sourceFilterFromMetadata } from "@/lib/archive-filters"
import type { ArchiveItem, ArchiveQuery } from "@/repositories/archive-types"
import type { LibraryFolder } from "@/lib/library-types"

/** Same buckets as the server index: paused, processing, failed (incl. cancelled), completed. */
export function archiveStatus(item: Pick<ArchiveItem, "processing" | "metadata">): "processing" | "paused" | "failed" | "completed" {
  const status = typeof item.metadata?.status === "string" ? item.metadata.status : ""
  if (status === "paused") return "paused"
  if (item.processing) return "processing"
  if (status === "failed" || status === "cancelled") return "failed"
  return "completed"
}

function listValues(value: string | undefined): string[] {
  return (value ?? "").split(",").map((item) => item.trim()).filter((item) => item && item !== "all")
}

function mediaMatches(item: ArchiveItem, media: string): boolean {
  if (media === "video") return item.has_video
  if (media === "audio") return !item.has_video && !item.has_image && item.has_audio
  return item.has_image || ["image_note", "text_note"].includes(String(item.metadata.content_subtype))
}

/** Offline copy of the library filters; values within one filter are OR-ed, like the server. */
export function queryLocalArchives(archives: ArchiveItem[], query: ArchiveQuery, folders: LibraryFolder[] = []) {
  const search = query.search.toLowerCase()
  const media = listValues(query.media)
  const sources = listValues(query.source)
  const statuses = listValues(query.status).filter((item) => item !== "duplicates")
  const items = archives.filter((item) => {
    if (query.folder_id && item.folder_id !== query.folder_id) return false
    if (query.unclassified && item.folder_id) return false
    if (media.length && !media.some((value) => mediaMatches(item, value))) return false
    if (sources.length && !sources.includes(sourceFilterFromMetadata(item.metadata))) return false
    if (statuses.length && !statuses.includes(archiveStatus(item))) return false
    return !query.search.trim() || item.title.toLowerCase().includes(search)
  })
  const timestamp = (value: unknown) => typeof value === "string" ? new Date(value).getTime() || 0 : 0
  const mixed = !!query.include_folders && !query.folder_id && !!query.unclassified
  const visibleFolders = mixed ? folders.filter((folder) => !query.search.trim() || folder.name.toLowerCase().includes(search)) : []
  const entries = items.map((item) => ({ kind: "archive" as "archive" | "folder", id: item.archive_id ?? item.path,
    title: item.title, created: timestamp(item.created_at), published: timestamp(item.metadata.upload_date),
    processed: timestamp(item.last_processed_at || item.created_at), processing: !!item.processing }))
  entries.push(...visibleFolders.map((folder) => ({ kind: "folder" as const, id: folder.id,
    title: folder.name, created: timestamp(folder.created_at), published: timestamp(folder.last_processed_at),
    processed: timestamp(folder.last_processed_at), processing: false })))
  entries.sort((left, right) => {
    if (!mixed && query.sort !== "processed_desc" && left.processing !== right.processing) return left.processing ? -1 : 1
    let compared = 0
    if (query.sort === "processed_desc") compared = right.processed - left.processed
    else if (query.sort === "created_asc") compared = left.created - right.created
    else if (query.sort === "published_desc") compared = right.published - left.published
    else if (query.sort === "title_asc") compared = left.title.localeCompare(right.title, "zh-CN")
    else compared = right.created - left.created
    return compared || left.id.localeCompare(right.id)
  })
  const page = Math.max(1, Math.min(query.page, Math.max(1, Math.ceil(entries.length / query.page_size))))
  const offset = query.offset ?? (page - 1) * query.page_size
  const selected = entries.slice(offset, offset + query.page_size)
  const archiveMap = new Map(items.map((item) => [item.archive_id ?? item.path, item]))
  const folderMap = new Map(visibleFolders.map((folder) => [folder.id, folder]))
  return { archives: selected.flatMap((entry) => entry.kind === "archive" ? [archiveMap.get(entry.id)!] : []),
    ...(mixed ? { folders: selected.flatMap((entry) => entry.kind === "folder" ? [folderMap.get(entry.id)!] : []),
      entries: selected.map(({ kind, id }) => ({ kind, id })), archive_total: items.length } : {}),
    total: entries.length, page, page_size: query.page_size }
}
