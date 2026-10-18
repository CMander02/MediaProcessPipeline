// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { LibraryPage } from "./library-page"
import { useLibraryControls } from "@/hooks/use-library-controls"
import { goLibraryHistory, libraryHash, navigate, parseHash, previousHash, useRoute } from "@/lib/router"
import type { LibraryFolder } from "@/lib/library-types"
import type { ArchiveItem } from "@/repositories/archive-types"

const mocks = vi.hoisted(() => ({
  resolve: vi.fn(),
  refresh: vi.fn(),
  updatePreferences: vi.fn(),
  platform: { kind: "web", isNative: false },
}))

const archives: ArchiveItem[] = ["a", "b"].map((id) => ({
  archive_id: id,
  content_key: `content-${id}`,
  path: `/archives/${id}`,
  task_id: `task-${id}`,
  folder_id: "course",
  title: `视频 ${id}`,
  created_at: "2026-10-01",
  date: "2026-10-01",
  metadata: {},
  has_video: true,
  has_audio: false,
  has_image: false,
  has_thumbnail: false,
  has_transcript: true,
  has_summary: true,
  has_mindmap: false,
  media_file: null,
  duration_seconds: 10,
  analysis: {},
}))
const folders: LibraryFolder[] = [{
  id: "course", name: "课程", count: 2, source_collection_key: null,
  created_at: "2026-10-01", updated_at: "2026-10-01",
}]
const emptyArchives: ArchiveItem[] = []
const folderEntries = [{ kind: "folder", id: "course" }]

vi.mock("@/hooks/use-archive-page", () => ({
  useArchivePage: (query: { unclassified?: boolean; include_folders?: boolean }) => {
    const pageArchives = query.unclassified ? emptyArchives : archives
    return {
      archives: pageArchives,
      // Archive-only API responses may omit folders entirely.
      ...(query.include_folders ? { folders, entries: folderEntries } : {}),
      total: pageArchives.length + (query.include_folders ? folders.length : 0), loading: false,
      refresh: mocks.refresh, removeArchive: vi.fn(), lastReconciledAt: new Date().toISOString(),
    }
  },
}))
vi.mock("@/hooks/use-library-directory", () => ({
  useLibraryDirectory: () => ({ folders }),
  libraryDirectorySnapshot: () => ({ folders }),
  libraryChanged: vi.fn(),
}))
vi.mock("@/hooks/use-active-tasks", () => ({ useActiveTasks: () => ({ tasks: [] }), refreshActiveTasks: vi.fn() }))
vi.mock("@/hooks/use-app-access-context", () => ({
  useAppAccess: () => ({ online: true, capabilities: { archive_mutation: false } }),
}))
vi.mock("@/platform/use-platform", () => ({ usePlatform: () => mocks.platform }))
vi.mock("@/hooks/use-preferences", () => ({ usePreferences: () => ({ update: mocks.updatePreferences }) }))
vi.mock("@/lib/api", () => ({
  getApiClientConfig: () => ({ baseUrl: "http://localhost" }),
  api: { library: { resolve: mocks.resolve }, archives: { thumbnailUrl: () => "/thumbnail.jpg" }, tasks: {} },
}))
vi.mock("./result-page-complete", () => ({
  ResultPageComplete: ({ archivePath, onTarget }: {
    archivePath: string
    onTarget: (path: string, taskId: string) => void
  }) => <section aria-label="内容详情">
    <p>{archivePath}</p>
    <button onClick={() => onTarget("/archives/a-reprocessed", "task-a-reprocessed")}>切换处理版本</button>
  </section>,
}))
vi.mock("./result-page-live", () => ({ ResultPageLive: () => <section aria-label="处理中的内容" /> }))

// Keep LibraryPage, FilesPage, ArchiveCard, library controls and the router real.
// This shell follows the same route boundary as the app so a grid click must
// leave the library to display a standalone result.
function RoutedLibrary() {
  const route = useRoute()
  const library = useLibraryControls()
  if (route.page === "result") return <main aria-label="独立内容详情">{route.resultId}</main>
  if (route.page === "settings") return <main aria-label="设置页面" />
  return <LibraryPage filters={library} page={library.page} onPageChange={library.setPage} />
}

function viewport(width: number) {
  vi.stubGlobal("matchMedia", vi.fn((query: string) => ({
    matches: width <= Number(query.match(/max-width:\s*(\d+)px/)?.[1] ?? 0),
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  })))
}

function openLibrary(hash: string) {
  act(() => navigate(hash))
  return render(<RoutedLibrary />)
}

beforeEach(() => {
  vi.clearAllMocks()
  viewport(1440)
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} })
  mocks.resolve.mockImplementation(async (key: string) => ({
    archive: archives.find((archive) => archive.content_key === key) ?? null,
    task_id: archives.find((archive) => archive.content_key === key)?.task_id ?? null,
  }))
  act(() => navigate("#/settings"))
})

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe("library view navigation", () => {
  it("opens a grid card as a standalone result and preserves the grid location", async () => {
    const gridLocation = "#/files?q=video&folder=course"
    openLibrary(gridLocation)

    fireEvent.click(screen.getByText("视频 a").closest("button")!)

    expect(await screen.findByRole("main", { name: "独立内容详情" })).toBeTruthy()
    expect(parseHash(window.location.hash)).toMatchObject({
      page: "result", resultType: "archive", resultId: "/archives/a", taskId: "task-a",
    })
    expect(libraryHash()).toBe(gridLocation)
    expect(screen.queryByRole("complementary", { name: "文件列表" })).toBeNull()
    expect(mocks.resolve).not.toHaveBeenCalled()
    expect(mocks.updatePreferences).toHaveBeenCalledWith({ lastArchivePath: "/archives/a" })
  })

  it.each([390, 1024])("uses list → detail → list navigation at %i px without automatically opening an item", async (width) => {
    viewport(width)
    openLibrary("#/files?view=split&scope=all")

    expect(screen.getByRole("complementary", { name: "文件列表" })).toBeTruthy()
    expect(parseHash(window.location.hash).params.item).toBeUndefined()
    expect(mocks.resolve).not.toHaveBeenCalled()
    expect(screen.queryByRole("region", { name: "内容详情" })).toBeNull()

    fireEvent.click(screen.getByRole("button", { name: /视频 b/ }))

    expect((await screen.findByRole("region", { name: "内容详情" })).textContent).toContain("/archives/b")
    expect(parseHash(window.location.hash).params.item).toBe("content-b")
    expect(screen.queryByRole("complementary", { name: "文件列表" })).toBeNull()

    fireEvent.click(screen.getByRole("button", { name: "返回列表" }))

    await waitFor(() => expect(parseHash(window.location.hash).params.item).toBeUndefined())
    expect(screen.getByRole("complementary", { name: "文件列表" })).toBeTruthy()
    expect(screen.queryByRole("region", { name: "内容详情" })).toBeNull()
    expect(screen.queryByRole("button", { name: "返回列表" })).toBeNull()
    expect(mocks.resolve).toHaveBeenCalledTimes(1)
  })

  it("renders an archive-only API response without folders and automatically opens the first item on desktop", async () => {
    openLibrary("#/files?view=split&scope=all")

    expect((await screen.findByRole("region", { name: "内容详情" })).textContent).toContain("/archives/a")
    expect(screen.getByRole("complementary", { name: "文件列表" })).toBeTruthy()
    expect(screen.getByRole("button", { name: /视频 a/ }).getAttribute("aria-current")).toBe("true")
    expect(parseHash(window.location.hash).params.item).toBe("content-a")
    expect(screen.queryByRole("button", { name: "返回列表" })).toBeNull()
  })

  it.each(["split", "browse"])("returns from an expanded folder to the original %s root list without leaving the closed detail behind in Back history", async (view) => {
    viewport(390)
    const rootLocation = `#/files?view=${view}&q=course`
    openLibrary(rootLocation)
    fireEvent.click(screen.getByRole("button", { name: /课程/ }))
    fireEvent.click(screen.getByRole("button", { name: /视频 a/ }))

    expect((await screen.findByRole("region", { name: "内容详情" })).textContent).toContain("/archives/a")
    expect(parseHash(window.location.hash).params).toMatchObject({ folder: "course", item: "content-a" })
    expect(previousHash()).toBe(rootLocation)

    fireEvent.click(screen.getByRole("button", { name: "返回列表" }))

    await waitFor(() => expect(window.location.hash).toBe(rootLocation))
    expect(screen.getByRole("button", { name: /课程/ }).getAttribute("aria-expanded")).toBe("true")
    expect(screen.getByRole("button", { name: /视频 a/ })).toBeTruthy()
    expect(previousHash()).toBe("#/settings")

    act(() => goLibraryHistory("back"))
    expect(await screen.findByRole("main", { name: "设置页面" })).toBeTruthy()
    expect(window.location.hash).toBe("#/settings")
    expect(screen.queryByRole("region", { name: "内容详情" })).toBeNull()
  })

  it("keeps the original mobile list directly behind a detail after switching its processing version", async () => {
    viewport(390)
    const listLocation = "#/files?view=split&scope=all"
    openLibrary(listLocation)
    fireEvent.click(screen.getByRole("button", { name: /视频 a/ }))
    await screen.findByRole("region", { name: "内容详情" })
    expect(previousHash()).toBe(listLocation)

    fireEvent.click(screen.getByRole("button", { name: "切换处理版本" }))

    await waitFor(() => expect(screen.getByRole("region", { name: "内容详情" }).textContent).toContain("/archives/a-reprocessed"))
    expect(previousHash()).toBe(listLocation)
    fireEvent.click(screen.getByRole("button", { name: "返回列表" }))

    await waitFor(() => expect(window.location.hash).toBe(listLocation))
    expect(screen.getByRole("complementary", { name: "文件列表" })).toBeTruthy()
    expect(screen.queryByRole("region", { name: "内容详情" })).toBeNull()
    expect(previousHash()).toBe("#/settings")
  })

  it("opens a mobile detail link directly and clears its selected version when returning to the list", async () => {
    viewport(390)
    openLibrary("#/files?view=split&scope=all&item=content-a&attempt=%2Farchives%2Fa-older&viewerTask=task-old&generation=1")

    expect((await screen.findByRole("region", { name: "内容详情" })).textContent).toContain("/archives/a-older")
    expect(screen.queryByRole("complementary", { name: "文件列表" })).toBeNull()

    fireEvent.click(screen.getByRole("button", { name: "返回列表" }))

    await waitFor(() => expect(screen.getByRole("complementary", { name: "文件列表" })).toBeTruthy())
    expect(parseHash(window.location.hash).params).toEqual({ view: "split", scope: "all" })
    expect(screen.queryByRole("region", { name: "内容详情" })).toBeNull()
  })

  it("keeps processing-version navigation working when entered through an old browse link", async () => {
    openLibrary("#/files?view=browse&scope=all&item=content-a")

    expect((await screen.findByRole("region", { name: "内容详情" })).textContent).toContain("/archives/a")
    fireEvent.click(screen.getByRole("button", { name: "切换处理版本" }))

    await waitFor(() => expect(screen.getByRole("region", { name: "内容详情" }).textContent).toContain("/archives/a-reprocessed"))
    expect(parseHash(window.location.hash).params).toMatchObject({
      item: "content-a", attempt: "/archives/a-reprocessed", viewerTask: "task-a-reprocessed",
    })
    expect(parseHash(window.location.hash).params.generation).toBeTruthy()
  })
})
