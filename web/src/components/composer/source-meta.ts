import { api, type BilibiliCollectionResult, type LibraryMatch } from "@/lib/api"
import type { SourceEntry } from "@/lib/source-input"

/** What a row can show about a link before it is processed. */
export interface SourceMeta {
  title?: string
  uploader?: string
  duration?: number | null
  subtitles?: string[]
  autoCaptions?: boolean
  collection?: BilibiliCollectionResult
}

// Metadata lookups run yt-dlp on the server; keep at most two in flight.
let running = 0
const waiting: Array<() => void> = []

function limited<T>(task: () => Promise<T>): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const start = () => {
      running += 1
      task().then(resolve, reject).finally(() => {
        running -= 1
        waiting.shift()?.()
      })
    }
    if (running < 2) start()
    else waiting.push(start)
  })
}

// Cached for the whole session so reopening the composer doesn't ask again.
const metaCache = new Map<string, Promise<SourceMeta | null>>()
const collectionCache = new Map<string, Promise<BilibiliCollectionResult | null>>()

export function inspectBilibili(url: string): Promise<BilibiliCollectionResult | null> {
  let pending = collectionCache.get(url)
  if (!pending) {
    pending = api.pipeline.bilibiliCollection(url).catch(() => null)
    collectionCache.set(url, pending)
  }
  return pending
}

export function isMultiPart(result: BilibiliCollectionResult | null | undefined): result is BilibiliCollectionResult {
  return Boolean(result?.is_collection && result.items.length > 1)
}

/** Metadata for platforms where a quick lookup is cheap and useful; null for the rest. */
export function fetchSourceMeta(entry: SourceEntry): Promise<SourceMeta | null> {
  if (entry.kind !== "url" || (entry.platform !== "bilibili" && entry.platform !== "youtube")) {
    return Promise.resolve(null)
  }
  let pending = metaCache.get(entry.key)
  if (!pending) {
    pending = entry.platform === "bilibili"
      ? inspectBilibili(entry.source).then((collection) => {
        if (!collection) return null
        const current = collection.items.find((item) => item.id === collection.current_item_id) ?? collection.items[0]
        // A multi-part video or season is named after the whole; its parts are picked below the row.
        if (collection.is_collection && collection.items.length > 1) return { title: collection.title || current?.title, duration: null, collection }
        return { title: current?.title ?? collection.title, duration: current?.duration ?? null, collection }
      })
      : limited(() => api.pipeline.probe(entry.source)).then((probe) => ({
        title: probe.title,
        uploader: probe.uploader,
        duration: probe.duration ?? null,
        subtitles: probe.subtitles ?? [],
        autoCaptions: probe.auto_captions ?? false,
      }), () => null)
    metaCache.set(entry.key, pending)
  }
  return pending
}

export type LibraryState =
  | { kind: "completed"; match: LibraryMatch }
  | { kind: "processing"; match: LibraryMatch }
  | { kind: "paused"; match: LibraryMatch }
  | { kind: "failed"; match: LibraryMatch; count: number }

/** How a link relates to what is already in the library. */
export function libraryStateOf(matches: LibraryMatch[] | undefined): LibraryState | null {
  if (!matches?.length) return null
  const paused = matches.find((match) => match.status === "paused")
  if (paused) return { kind: "paused", match: paused }
  const active = matches.find((match) => match.processing)
  if (active) return { kind: "processing", match: active }
  // Archives from before task status was recorded count as finished, as in the library filter.
  const done = matches.find((match) => match.status !== "failed" && match.status !== "cancelled")
  if (done) return { kind: "completed", match: done }
  return { kind: "failed", match: matches[0], count: matches.length }
}
