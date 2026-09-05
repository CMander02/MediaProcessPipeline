import { Capacitor } from "@capacitor/core"

import { api } from "@/lib/api"
import type { PlatformAdapter, OfflineArchiveRecord } from "@/platform"
import type { ArchiveItem, ArchivePage, ArchiveQuery, OfflineFileDescriptor } from "@/repositories/archive-types"
import { queryLocalArchives } from "@/lib/archive-query"

const OFFLINE_PREFIX = "offline://"
let activeSync: Promise<void> | null = null
const offlineFileCache = new Map<string, Map<string, string>>()

export interface ArchiveRepository {
  list(lite?: boolean): Promise<ArchiveItem[]>
  listPage(query: ArchiveQuery): Promise<ArchivePage>
  get(path: string): Promise<ArchiveItem | null>
  readFile(path: string): Promise<string>
  listFiles(archive: ArchiveItem, directory: string): OfflineFileDescriptor[]
  fileUrl(archive: ArchiveItem, relativePath: string): string | null
  thumbnailUrl(archive: ArchiveItem): string
  triggerSync(): void
}

export function isOfflineArchivePath(path: string): boolean {
  return path.startsWith(OFFLINE_PREFIX)
}

export function resolveOfflineFileUrl(archivePath: string, relativePath: string): string | null {
  const parsed = parseOfflinePath(archivePath)
  if (!parsed) return null
  return offlineFileCache.get(parsed.archiveId)?.get(normalizeRelativePath(relativePath)) ?? null
}

export function createArchiveRepository(platform: PlatformAdapter): ArchiveRepository {
  if (!platform.isNative) return createWebArchiveRepository()

  const normalize = (record: OfflineArchiveRecord): ArchiveItem => {
    const offlineFiles = (record.offline_files ?? []).map((file) => ({
      relativePath: file.relative_path,
      url: Capacitor.convertFileSrc(file.uri),
      size: file.size,
      mime: file.mime,
    }))
    offlineFileCache.set(
      String(record.archive_id),
      new Map(offlineFiles.map((file) => [file.relativePath, file.url])),
    )
    const thumbnailUrl = ["thumbnail.jpg", "thumbnail.webp", "thumbnail.png", "cover.jpg", "cover.webp", "cover.png"]
      .map((candidate) => offlineFiles.find((file) => file.relativePath === candidate)?.url)
      .find(Boolean)
    return {
      ...(record as unknown as ArchiveItem),
      path: String(record.path),
      archive_id: String(record.archive_id),
      server_path: typeof record.server_path === "string" ? record.server_path : undefined,
      offline: true,
      offlineFiles,
      thumbnail_url: thumbnailUrl,
    }
  }

  const triggerSync = () => {
    if (activeSync) return
    activeSync = platform.syncOfflineArchives()
      .then(() => undefined)
      .catch(() => undefined)
      .finally(() => {
        activeSync = null
        window.dispatchEvent(new Event("mpp:offline-library-change"))
      })
  }

  return {
    async list() {
      return (await platform.listOfflineArchives()).map(normalize)
    },
    async listPage(query) {
      const items = (await platform.listOfflineArchives()).map(normalize)
      return { ...queryLocalArchives(items, query), workspace_id: "offline", revision: 0,
        last_reconciled_at: null, indexing: false }
    },
    async get(path) {
      const parsed = parseOfflinePath(path)
      if (!parsed) {
        try {
          return (await api.archives.get(path)).archive as ArchiveItem
        } catch {
          return null
        }
      }
      const record = await platform.getOfflineArchive(parsed.archiveId)
      return record ? normalize(record) : null
    },
    async readFile(path) {
      const parsed = parseOfflinePath(path)
      if (!parsed) {
        try {
          return (await api.filesystem.read(path)).content ?? ""
        } catch {
          return ""
        }
      }
      if (!parsed.relativePath) return ""
      try {
        return await platform.readOfflineText(parsed.archiveId, parsed.relativePath)
      } catch {
        return ""
      }
    },
    listFiles(archive, directory) {
      const prefix = `${directory.replace(/^\/+|\/+$/g, "")}/`
      return (archive.offlineFiles ?? []).filter((file) => file.relativePath.startsWith(prefix))
    },
    fileUrl(archive, relativePath) {
      if (!archive.offline) return api.filesystem.mediaUrl(relativePath)
      const normalized = normalizeRelativePath(relativePath)
      return archive.offlineFiles?.find((file) => file.relativePath === normalized)?.url ?? null
    },
    thumbnailUrl(archive) {
      if (!archive.offline) return api.archives.thumbnailUrl(archive.path)
      const candidates = ["thumbnail.jpg", "thumbnail.webp", "thumbnail.png", "cover.jpg", "cover.webp", "cover.png"]
      for (const candidate of candidates) {
        const url = archive.offlineFiles?.find((file) => file.relativePath === candidate)?.url
        if (url) return url
      }
      return ""
    },
    triggerSync,
  }
}

function createWebArchiveRepository(): ArchiveRepository {
  return {
    listPage: (query) => api.archives.page(query),
    async list(lite = true) {
      const data = await api.archives.list({ lite })
      return (data.archives ?? []) as ArchiveItem[]
    },
    async get(path) {
      const data = await api.archives.get(path)
      return data.archive as ArchiveItem
    },
    async readFile(path) {
      try {
        return (await api.filesystem.read(path)).content ?? ""
      } catch {
        return ""
      }
    },
    listFiles() { return [] },
    fileUrl(_archive, relativePath) {
      return api.filesystem.mediaUrl(relativePath)
    },
    thumbnailUrl(archive) {
      return api.archives.thumbnailUrl(archive.path)
    },
    triggerSync() {},
  }
}

function parseOfflinePath(path: string): { archiveId: string; relativePath: string } | null {
  if (!isOfflineArchivePath(path)) return null
  const rest = path.slice(OFFLINE_PREFIX.length)
  const separator = rest.indexOf("/")
  if (separator < 0) return { archiveId: rest, relativePath: "" }
  return {
    archiveId: rest.slice(0, separator),
    relativePath: normalizeRelativePath(rest.slice(separator + 1)),
  }
}

function normalizeRelativePath(path: string): string {
  return path.replace(/\\/g, "/").replace(/^\.\//, "").replace(/^\/+/, "")
}
