import { sourceFilterFromMetadata } from "@/lib/archive-filters"
import type { ArchiveItem, ArchiveQuery } from "@/repositories/archive-types"

export function queryLocalArchives(archives: ArchiveItem[], query: ArchiveQuery) {
  const search = query.search.toLowerCase()
  const items = archives.filter((item) => {
    if (query.media === "video" && !item.has_video) return false
    if (query.media === "audio" && (item.has_video || item.has_image || !item.has_audio)) return false
    if (query.media === "image" && !item.has_image
      && !["image_note", "text_note"].includes(String(item.metadata.content_subtype))) return false
    if (query.source !== "all" && sourceFilterFromMetadata(item.metadata) !== query.source) return false
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
