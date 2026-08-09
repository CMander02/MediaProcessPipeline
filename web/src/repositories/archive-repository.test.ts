/** @vitest-environment jsdom */

import { describe, expect, it, vi } from "vitest"

import { api } from "@/lib/api"
import type { PlatformAdapter } from "@/platform"
import { createArchiveRepository, resolveOfflineFileUrl } from "@/repositories/archive-repository"

function nativePlatform(): PlatformAdapter {
  return {
    kind: "android",
    isNative: true,
    initialize: vi.fn(),
    getConnection: vi.fn(),
    connect: vi.fn(),
    clearToken: vi.fn(),
    clearConnection: vi.fn(),
    applyCapabilities: vi.fn((value) => value),
    getNetworkStatus: vi.fn(),
    openExternal: vi.fn(),
    download: vi.fn(),
    saveTextFile: vi.fn(),
    consumeSharedText: vi.fn(() => null),
    syncTheme: vi.fn(),
    getOfflineSyncStatus: vi.fn(),
    listOfflineArchives: vi.fn(async () => [{
      archive_id: "8e4969ed-508d-4510-a7de-94dcd9bf70f2",
      path: "offline://8e4969ed-508d-4510-a7de-94dcd9bf70f2",
      server_path: "D:\\MPP\\demo",
      title: "离线归档",
      date: "2026-08-09",
      created_at: "2026-08-09T12:00:00+08:00",
      has_transcript: true,
      has_summary: true,
      has_mindmap: false,
      has_video: true,
      has_audio: false,
      has_image: true,
      media_file: "D:\\MPP\\demo\\video.mp4",
      metadata: { title: "离线归档" },
      analysis: {},
      duration_seconds: 60,
      offline_files: [
        { relative_path: "thumbnail.jpg", sha256: "a", size: 10, mime: "image/jpeg", uri: "file:///private/thumbnail.jpg" },
        { relative_path: "images/00.jpg", sha256: "b", size: 20, mime: "image/jpeg", uri: "file:///private/images/00.jpg" },
      ],
    }]),
    getOfflineArchive: vi.fn(async () => null),
    readOfflineText: vi.fn(async (_archiveId, relativePath) => relativePath === "summary.md" ? "# 摘要" : ""),
    syncOfflineArchives: vi.fn(),
    clearOfflineArchives: vi.fn(),
    rebuildOfflineIndex: vi.fn(),
  } as PlatformAdapter
}

describe("Android ArchiveRepository", () => {
  it("maps the native SQLite record into the shared archive model", async () => {
    const repository = createArchiveRepository(nativePlatform())

    const [archive] = await repository.list()

    expect(archive.offline).toBe(true)
    expect(archive.server_path).toBe("D:\\MPP\\demo")
    expect(archive.thumbnail_url).toContain("thumbnail.jpg")
    expect(repository.listFiles(archive, "images")).toHaveLength(1)
    expect(resolveOfflineFileUrl(archive.path, "images/00.jpg")).toContain("images/00.jpg")
    await expect(repository.readFile(`${archive.path}/summary.md`)).resolves.toBe("# 摘要")
  })

  it("keeps online result paths available inside the Android shell", async () => {
    const repository = createArchiveRepository(nativePlatform())
    const archive = { path: "D:\\MPP\\online", title: "在线归档" }
    const getArchive = vi.spyOn(api.archives, "get").mockResolvedValue({ archive } as never)
    const readFile = vi.spyOn(api.filesystem, "read").mockResolvedValue({ success: true, content: "在线摘要" })

    await expect(repository.get(archive.path)).resolves.toMatchObject(archive)
    await expect(repository.readFile(`${archive.path}\\summary.md`)).resolves.toBe("在线摘要")
    expect(getArchive).toHaveBeenCalledWith(archive.path)
    expect(readFile).toHaveBeenCalled()
  })
})
