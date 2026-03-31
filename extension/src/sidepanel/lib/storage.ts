import type { AnalysisResult } from "../App"

interface CacheEntry {
  analysis: AnalysisResult["analysis"]
  summary: AnalysisResult["summary"]
  outline: AnalysisResult["outline"]
  timestamp: number
}

const MAX_CACHE_ENTRIES = 100

function cacheKey(platform: string, videoId: string): string {
  return `cache:${platform}:${videoId}`
}

export async function getCache(
  platform: string,
  videoId: string,
): Promise<AnalysisResult | null> {
  const key = cacheKey(platform, videoId)
  const result = await chrome.storage.local.get(key)
  const entry: CacheEntry | undefined = result[key]
  if (!entry) return null
  return {
    analysis: entry.analysis,
    summary: entry.summary,
    outline: entry.outline,
  }
}

export async function setCache(
  platform: string,
  videoId: string,
  result: Omit<CacheEntry, "timestamp">,
): Promise<void> {
  const key = cacheKey(platform, videoId)
  const entry: CacheEntry = { ...result, timestamp: Date.now() }
  await chrome.storage.local.set({ [key]: entry })
  await evictOldEntries()
}

async function evictOldEntries(): Promise<void> {
  const all = await chrome.storage.local.get(null)
  const cacheEntries: Array<{ key: string; timestamp: number }> = []

  for (const [key, value] of Object.entries(all)) {
    if (key.startsWith("cache:") && typeof value === "object" && value !== null && "timestamp" in value) {
      cacheEntries.push({ key, timestamp: (value as CacheEntry).timestamp })
    }
  }

  if (cacheEntries.length <= MAX_CACHE_ENTRIES) return

  cacheEntries.sort((a, b) => a.timestamp - b.timestamp)
  const toRemove = cacheEntries.slice(0, cacheEntries.length - MAX_CACHE_ENTRIES)
  await chrome.storage.local.remove(toRemove.map((e) => e.key))
}
