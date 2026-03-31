import type { VideoData, SubtitleEntry } from "@/content/types"
import type { AnalysisResult } from "../App"

const DIR_HANDLE_DB = "mpp-export-handle"
const DIR_HANDLE_STORE = "handles"

function safeName(name: string): string {
  let safe = name
  for (const c of '<>:"/\\|?*') {
    safe = safe.replaceAll(c, "_")
  }
  return safe.slice(0, 100).trim()
}

function subtitlesToSRT(subtitles: SubtitleEntry[]): string {
  return subtitles
    .map((sub, i) => {
      const fmt = (totalSec: number) => {
        const h = Math.floor(totalSec / 3600)
        const m = Math.floor((totalSec % 3600) / 60)
        const s = Math.floor(totalSec % 60)
        const ms = Math.floor((totalSec % 1) * 1000)
        return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")},${String(ms).padStart(3, "0")}`
      }
      return `${i + 1}\n${fmt(sub.start)} --> ${fmt(sub.end)}\n${sub.text}`
    })
    .join("\n\n")
}

async function getPersistedHandle(): Promise<FileSystemDirectoryHandle | null> {
  try {
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      const req = indexedDB.open(DIR_HANDLE_DB, 1)
      req.onupgradeneeded = () => req.result.createObjectStore(DIR_HANDLE_STORE)
      req.onsuccess = () => resolve(req.result)
      req.onerror = () => reject(req.error)
    })
    const tx = db.transaction(DIR_HANDLE_STORE, "readonly")
    const store = tx.objectStore(DIR_HANDLE_STORE)
    const handle = await new Promise<FileSystemDirectoryHandle | undefined>((resolve) => {
      const req = store.get("exportDir")
      req.onsuccess = () => resolve(req.result)
      req.onerror = () => resolve(undefined)
    })
    db.close()
    if (!handle) return null
    const perm = await handle.requestPermission({ mode: "readwrite" })
    return perm === "granted" ? handle : null
  } catch {
    return null
  }
}

async function persistHandle(handle: FileSystemDirectoryHandle): Promise<void> {
  const db = await new Promise<IDBDatabase>((resolve, reject) => {
    const req = indexedDB.open(DIR_HANDLE_DB, 1)
    req.onupgradeneeded = () => req.result.createObjectStore(DIR_HANDLE_STORE)
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
  const tx = db.transaction(DIR_HANDLE_STORE, "readwrite")
  tx.objectStore(DIR_HANDLE_STORE).put(handle, "exportDir")
  await new Promise<void>((resolve) => { tx.oncomplete = () => resolve() })
  db.close()
}

async function getExportDir(): Promise<FileSystemDirectoryHandle> {
  const persisted = await getPersistedHandle()
  if (persisted) return persisted
  const handle = await window.showDirectoryPicker({ mode: "readwrite" })
  await persistHandle(handle)
  return handle
}

async function writeFile(
  dir: FileSystemDirectoryHandle,
  name: string,
  content: string,
): Promise<void> {
  const file = await dir.getFileHandle(name, { create: true })
  const writable = await file.createWritable()
  await writable.write(content)
  await writable.close()
}

export async function exportToLocal(
  videoData: VideoData,
  result: AnalysisResult,
): Promise<void> {
  const rootDir = await getExportDir()
  const dirName = safeName(videoData.title)
  const subDir = await rootDir.getDirectoryHandle(dirName, { create: true })

  const metadata = {
    title: videoData.title,
    source_url:
      videoData.platform === "bilibili"
        ? `https://www.bilibili.com/video/${videoData.videoId}`
        : `https://www.youtube.com/watch?v=${videoData.videoId}`,
    platform: videoData.platform,
    uploader: videoData.uploader,
    duration_seconds: videoData.duration,
    subtitle_lang: videoData.rawSubtitleLang,
    exported_at: new Date().toISOString(),
  }
  await writeFile(subDir, "metadata.json", JSON.stringify(metadata, null, 2))

  if (videoData.subtitles.length > 0) {
    await writeFile(subDir, "transcript.srt", subtitlesToSRT(videoData.subtitles))
  }

  if (result.analysis) {
    await writeFile(subDir, "analysis.json", JSON.stringify(result.analysis, null, 2))
  }

  if (result.summary) {
    const summaryMd = `---
title: "${videoData.title}"
source: "${metadata.source_url}"
date: ${new Date().toISOString().split("T")[0]}
tags: [media-pipeline]
---

# ${videoData.title}

## Summary
${result.summary.tldr}

### Key Facts
${result.summary.key_facts.map((f) => `- ${f}`).join("\n")}
`
    await writeFile(subDir, "summary.md", summaryMd)
  }

  if (result.outline) {
    await writeFile(subDir, "outline.md", result.outline)
  }
}
