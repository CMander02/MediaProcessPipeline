import { sourceFilterFromMetadata } from "@/lib/archive-filters"
import type { ArchiveItem, ArchiveQuery } from "@/repositories/archive-types"

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
export function queryLocalArchives(archives: ArchiveItem[], query: ArchiveQuery) {
  const search = query.search.toLowerCase()
  const media = listValues(query.media)
  const sources = listValues(query.source)
  const statuses = listValues(query.status).filter((item) => item !== "duplicates")
  const items = archives.filter((item) => {
    if (media.length && !media.some((value) => mediaMatches(item, value))) return false
    if (sources.length && !sources.includes(sourceFilterFromMetadata(item.metadata))) return false
    if (statuses.length && !statuses.includes(archiveStatus(item))) return false
    return !query.search.trim() || item.title.toLowerCase().includes(search)
  })
  const timestamp = (value: unknown) => typeof value === "string" ? new Date(value).getTime() || 0 : 0
  items.sort((left, right) => {
    if (!!left.processing !== !!right.processing) return left.processing ? -1 : 1
    let compared = 0
    if (query.sort === "created_asc") compared = timestamp(left.created_at) - timestamp(right.created_at)
    else if (query.sort === "published_desc") compared = timestamp(right.metadata.upload_date) - timestamp(left.metadata.upload_date)
    else if (query.sort === "title_asc") compared = left.title.localeCompare(right.title, "zh-CN")
    else compared = timestamp(right.created_at) - timestamp(left.created_at)
    return compared || (left.archive_id ?? left.path).localeCompare(right.archive_id ?? right.path)
  })
  const page = Math.max(1, Math.min(query.page, Math.max(1, Math.ceil(items.length / query.page_size))))
  return { archives: items.slice((page - 1) * query.page_size, page * query.page_size),
    total: items.length, page, page_size: query.page_size }
}
