/**
 * Turns pasted text into submittable sources: one per link, Bilibili BV id or local path.
 * Share texts ("【标题】 https://b23.tv/xx 复制打开") keep only the link.
 */

export type SourceKind = "url" | "path"

export interface SourceEntry {
  /** Identity used to drop repeats, e.g. "youtube:abc123" */
  key: string
  /** What gets submitted */
  source: string
  /** The text it was read from, so the row can be removed from the input */
  raw: string
  kind: SourceKind
  /** Icon key for PlatformIcon; null for local paths */
  platform: string | null
}

// Stops at whitespace, quotes and the CJK punctuation share texts wrap links in.
const URL_RE = /https?:\/\/[^\s<>"'`，。、；！？（）【】《》「」]+/gi
const BV_RE = /\bBV[0-9A-Za-z]{10}\b/
const WINDOWS_PATH_RE = /^(?:[A-Za-z]:[\\/]|\\\\[^\\]+\\)/
const MEDIA_EXT_RE = /\.(?:mp4|mkv|avi|webm|mov|flv|wmv|m4v|ts|mts|mp3|wav|flac|m4a|aac|ogg|opus|wma|amr)$/i

function trimUrl(value: string): string {
  // Sentence punctuation right after a link is almost never part of it.
  return value.replace(/[.,;:!?)\]}>]+$/, "")
}

export function platformOf(url: string): string {
  let host = ""
  try {
    host = new URL(url).hostname.toLowerCase().replace(/^(?:www|m)\./, "")
  } catch {
    return "webpage"
  }
  if (host.endsWith("bilibili.com") || host === "b23.tv") return "bilibili"
  if (host.endsWith("youtube.com") || host === "youtu.be") return "youtube"
  if (host.endsWith("xiaohongshu.com") || host === "xhslink.com") return "xiaohongshu"
  if (host === "x.com" || host.endsWith("twitter.com")) return "x"
  if (host.endsWith("zhihu.com")) return "zhihu"
  if (host.endsWith("xiaoyuzhoufm.com")) return "xiaoyuzhou"
  if (host === "podcasts.apple.com") return "apple_podcast"
  return "webpage"
}

/** Same identity rules as the archive index, so "already in the library" and "listed twice" agree. */
export function sourceKey(value: string): string {
  let url: URL
  try {
    url = new URL(value)
  } catch {
    return value.trim().toLowerCase()
  }
  const host = url.hostname.toLowerCase().replace(/^(?:www|m)\./, "")
  const bv = value.match(BV_RE)
  if (bv) {
    const part = url.searchParams.get("p") ?? "1"
    return `bilibili:${bv[0]}:p${/^\d+$/.test(part) ? part : "1"}`
  }
  if (host === "youtu.be") return `youtube:${url.pathname.replace(/^\/+|\/+$/g, "")}`
  if (host.endsWith("youtube.com") && url.searchParams.get("v")) return `youtube:${url.searchParams.get("v")}`
  return `${host}${url.pathname.replace(/\/+$/, "")}`
}

export function looksLikeBilibiliVideo(value: string): boolean {
  return /(?:bilibili\.com\/video\/|b23\.tv\/|\bBV[0-9A-Za-z]{10}\b)/i.test(value)
}

export function isMediaFileName(name: string): boolean {
  return MEDIA_EXT_RE.test(name)
}

export function isMediaFile(file: File): boolean {
  return file.type.startsWith("video/") || file.type.startsWith("audio/") || isMediaFileName(file.name)
}

function stripQuotes(value: string): string {
  return value.trim().replace(/^["'“”]+|["'“”]+$/g, "").trim()
}

export interface ParsedSources {
  entries: SourceEntry[]
  /** Non-empty lines that held nothing submittable */
  unrecognized: string[]
}

export function parseSources(text: string, options: { allowPaths?: boolean } = {}): ParsedSources {
  const entries: SourceEntry[] = []
  const unrecognized: string[] = []
  const seen = new Set<string>()
  const add = (entry: Omit<SourceEntry, "key">) => {
    const key = entry.kind === "url" ? sourceKey(entry.source) : entry.source.toLowerCase()
    if (seen.has(key)) return
    seen.add(key)
    entries.push({ ...entry, key })
  }

  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim()
    if (!line) continue
    const urls = [...line.matchAll(URL_RE)].map((match) => ({ raw: match[0], url: trimUrl(match[0]) }))
    if (urls.length > 0) {
      for (const { raw, url } of urls) add({ source: url, raw, kind: "url", platform: platformOf(url) })
      continue
    }
    const bv = line.match(BV_RE)
    if (bv) {
      add({ source: `https://www.bilibili.com/video/${bv[0]}`, raw: bv[0], kind: "url", platform: "bilibili" })
      continue
    }
    const path = stripQuotes(line)
    if (options.allowPaths && (WINDOWS_PATH_RE.test(path) || path.startsWith("/"))) {
      add({ source: path, raw: rawLine.trim(), kind: "path", platform: null })
      continue
    }
    unrecognized.push(line)
  }
  return { entries, unrecognized }
}

/** Remove one entry from the input, with any share-text around it and lines left empty. */
export function removeSourceText(text: string, entry: SourceEntry): string {
  const kept: string[] = []
  for (const line of text.split(/\r?\n/)) {
    if (!line.includes(entry.raw)) {
      if (line.trim()) kept.push(line)
      continue
    }
    const rest = line.replace(entry.raw, "").trim()
    if (rest && parseSources(rest, { allowPaths: true }).entries.length > 0) kept.push(rest)
  }
  return kept.join("\n")
}
