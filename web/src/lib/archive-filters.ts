export type MediaFilter = "all" | "video" | "audio" | "image"

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
