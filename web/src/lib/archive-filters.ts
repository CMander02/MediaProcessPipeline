export type MediaFilter = "all" | "video" | "audio" | "image"

export type ArchiveSort = "created_desc" | "created_asc" | "published_desc" | "title_asc"

/** Library status of an archive; cancelled runs count as failed, as in the server index. */
export type ArchiveStatus = "processing" | "paused" | "failed" | "completed"

export const STATUS_OPTIONS: Array<{ value: ArchiveStatus; label: string; hint?: string }> = [
  { value: "processing", label: "处理中" },
  { value: "paused", label: "已暂停" },
  { value: "failed", label: "失败", hint: "处理失败或被取消" },
  { value: "completed", label: "已完成" },
]

export const SORT_OPTIONS: Array<{ value: ArchiveSort; label: string }> = [
  { value: "created_desc", label: "最新创建" },
  { value: "created_asc", label: "最早创建" },
  { value: "published_desc", label: "最新发布" },
  { value: "title_asc", label: "标题排序" },
]

export type SourceFilter =
  | "all"
  | "xiaohongshu"
  | "bilibili"
  | "youtube"
  | "x"
  | "webpage"
  | "zhihu"
  | "xiaoyuzhou"
  | "apple_podcast"
  | "local"
  | "other"

export interface MediaFilterOption {
  value: MediaFilter
  label: string
}

export interface SourceFilterOption {
  value: SourceFilter
  label: string
  platform?: string
}

/** A media type or source that can be picked in the filter (everything except "all"). */
export type MediaType = Exclude<MediaFilter, "all">
export type SourceKey = Exclude<SourceFilter, "all">

export const MEDIA_FILTER_OPTIONS: MediaFilterOption[] = [
  { value: "all", label: "全部" },
  { value: "video", label: "视频" },
  { value: "audio", label: "音频" },
  { value: "image", label: "图文" },
]

export const SOURCE_FILTER_OPTIONS: SourceFilterOption[] = [
  { value: "all", label: "全部来源" },
  { value: "xiaohongshu", label: "小红书", platform: "xiaohongshu" },
  { value: "bilibili", label: "Bilibili", platform: "bilibili" },
  { value: "youtube", label: "YouTube", platform: "youtube" },
  { value: "x", label: "X", platform: "x" },
  { value: "webpage", label: "Webpage", platform: "webpage" },
  { value: "zhihu", label: "知乎", platform: "zhihu" },
  { value: "xiaoyuzhou", label: "小宇宙", platform: "xiaoyuzhou" },
  { value: "apple_podcast", label: "Apple Podcasts", platform: "apple_podcast" },
  { value: "local", label: "本地文件" },
  { value: "other", label: "其他来源" },
]

export function normalizeSourceFilter(value: unknown): SourceFilter {
  if (typeof value !== "string") return "other"
  const key = value.trim().toLowerCase()
  if (!key) return "other"
  if (key === "xiaohongshu" || key === "xhs") return "xiaohongshu"
  if (key === "bilibili" || key === "bilibili_opus" || key === "bilibili_video" || key === "bili") return "bilibili"
  if (key === "youtube" || key === "yt") return "youtube"
  if (key === "twitter" || key === "x" || key === "x_twitter") return "x"
  if (key === "webpage" || key === "web" || key === "generic_webpage" || key === "url") return "webpage"
  if (key === "zhihu") return "zhihu"
  if (key === "xiaoyuzhou") return "xiaoyuzhou"
  if (key === "apple" || key === "apple_podcast") return "apple_podcast"
  if (key === "local" || key === "local_file" || key === "local_video" || key === "local_audio") return "local"
  return "other"
}

export function sourceFilterFromMetadata(metadata: Record<string, unknown> | undefined): SourceFilter {
  if (!metadata) return "other"
  const extra = metadata.extra
  const extraRecord = extra && typeof extra === "object" ? extra as Record<string, unknown> : {}
  const candidates = [
    metadata.platform,
    extraRecord.platform,
    metadata.source_type,
    metadata.media_type,
    metadata.content_subtype,
  ]
  for (const candidate of candidates) {
    const normalized = normalizeSourceFilter(candidate)
    if (normalized !== "other") return normalized
  }
  return "other"
}

export const MEDIA_TYPE_OPTIONS = MEDIA_FILTER_OPTIONS.filter(
  (option): option is MediaFilterOption & { value: MediaType } => option.value !== "all",
)
export const SOURCE_KEY_OPTIONS = SOURCE_FILTER_OPTIONS.filter(
  (option): option is SourceFilterOption & { value: SourceKey } => option.value !== "all",
)

/** Everything that narrows the library, as kept in the URL. */
export interface LibraryFilters {
  search: string
  media: MediaType[]
  sources: SourceKey[]
  statuses: ArchiveStatus[]
  /** Show every run of sources processed more than once */
  duplicates: boolean
  sort: ArchiveSort
}

export const DEFAULT_LIBRARY_FILTERS: LibraryFilters = {
  search: "", media: [], sources: [], statuses: [], duplicates: false, sort: "created_desc",
}

/** True when anything but the sort order narrows the list. */
export function hasActiveFilters(filters: LibraryFilters): boolean {
  return Boolean(filters.search.trim()) || filters.media.length > 0 || filters.sources.length > 0
    || filters.statuses.length > 0 || filters.duplicates
}
