import { type ArchiveItem } from "@/hooks/use-archives"

export function normalizeArchivePath(path: string): string {
  return path.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase()
}

export function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null
}

function firstHttpUrl(...values: unknown[]): string | null {
  for (const value of values) {
    if (typeof value !== "string") continue
    const trimmed = value.trim()
    if (!/^https?:\/\//i.test(trimmed)) continue
    try {
      new URL(trimmed)
      return trimmed
    } catch {
      continue
    }
  }
  return null
}

export function resolveSourceUrl(metadata: Record<string, unknown>): string | null {
  const extra = asRecord(metadata.extra)
  const nested = asRecord(extra?.metadata) ?? asRecord(extra?.raw) ?? asRecord(extra?.info)
  return firstHttpUrl(
    metadata.source_url,
    metadata.original_url,
    metadata.webpage_url,
    metadata.url,
    extra?.source_url,
    extra?.original_url,
    extra?.webpage_url,
    extra?.url,
    nested?.source_url,
    nested?.original_url,
    nested?.webpage_url,
    nested?.url,
  )
}

export function sourceUrlAtTime(sourceUrl: string, timeMs: number): string {
  const url = new URL(sourceUrl)
  const host = url.hostname
  const seconds = Math.floor(timeMs / 1000)
  if (host === "youtu.be" || ((host === "youtube.com" || host.endsWith(".youtube.com")) && url.pathname === "/watch" && url.searchParams.has("v"))) {
    url.searchParams.set("t", `${seconds}s`)
  } else if ((host === "bilibili.com" || host.endsWith(".bilibili.com")) && /^\/(?:video|bangumi\/play)\//.test(url.pathname)) {
    url.searchParams.set("t", String(seconds))
  } else {
    return sourceUrl
  }
  return url.href
}

export function firstTextValue(...values: unknown[]): string {
  for (const value of values) {
    if (typeof value !== "string") continue
    const trimmed = value.trim()
    if (trimmed) return trimmed
  }
  return ""
}

export function resolveRerunSource(metadata: Record<string, unknown>, archive: ArchiveItem | null, sourceUrl: string | null): string {
  const extra = asRecord(metadata.extra)
  const nested = asRecord(extra?.metadata) ?? asRecord(extra?.raw) ?? asRecord(extra?.info)
  return firstTextValue(
    metadata.source_url,
    metadata.original_url,
    metadata.webpage_url,
    metadata.file_path,
    metadata.url,
    extra?.source_url,
    extra?.original_url,
    extra?.webpage_url,
    extra?.file_path,
    extra?.url,
    nested?.source_url,
    nested?.original_url,
    nested?.webpage_url,
    nested?.file_path,
    nested?.url,
    sourceUrl,
    archive?.media_file,
  )
}
