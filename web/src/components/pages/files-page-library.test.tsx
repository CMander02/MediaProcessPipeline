// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { FilesPage } from "./files-page"
import { DEFAULT_LIBRARY_FILTERS } from "@/lib/archive-filters"
import type { ArchiveItem } from "@/repositories/archive-types"
import type { LibraryFolder } from "@/lib/library-types"

const mocks = vi.hoisted(() => ({ move: vi.fn(), remove: vi.fn(), refresh: vi.fn(), changed: vi.fn(), error: vi.fn() }))
const folders: LibraryFolder[] = [{ id: "target", name: "课程", count: 0, source_collection_key: null, created_at: "2026-10-01", updated_at: "2026-10-01" }]
const archives: ArchiveItem[] = ["a", "b"].map((id) => ({ archive_id: id, content_key: id, path: id, title: `视频 ${id}`,
  created_at: "2026-10-01", date: "2026-10-01", metadata: {}, has_video: true, has_audio: false, has_image: false,
  has_thumbnail: false, has_transcript: true, has_summary: true, has_mindmap: false, media_file: null, duration_seconds: 10, analysis: {} }))
const entries = [{ kind: "archive", id: "a" }, { kind: "folder", id: "target" }, { kind: "archive", id: "b" }]
vi.mock("@/hooks/use-archive-page", () => ({ useArchivePage: () => ({ archives, folders, entries, total: 3, archiveTotal: 2,
  loading: false, refresh: mocks.refresh, removeArchive: mocks.remove, lastReconciledAt: new Date().toISOString() }) }))
vi.mock("@/hooks/use-library-directory", () => ({ useLibraryDirectory: () => ({ folders }), libraryDirectorySnapshot: () => ({ folders }), libraryChanged: mocks.changed }))
vi.mock("@/hooks/use-app-access-context", () => ({ useAppAccess: () => ({ online: true, capabilities: { archive_mutation: true } }) }))
vi.mock("@/platform/use-platform", () => ({ usePlatform: () => ({ kind: "web", isNative: false }) }))
vi.mock("@/hooks/use-preferences", () => ({ usePreferences: () => ({ update: vi.fn() }) }))
vi.mock("@/hooks/use-active-tasks", () => ({ useActiveTasks: () => ({ tasks: [] }), refreshActiveTasks: vi.fn() }))
vi.mock("@/lib/router", () => ({ navigate: vi.fn() }))
vi.mock("@/lib/notify", () => ({ notifySuccess: vi.fn(), notifyError: mocks.error, errorMessage: (value: unknown) => String(value) }))
vi.mock("@/lib/api", () => ({ getApiClientConfig: () => ({ baseUrl: "http://localhost" }), api: { library: { move: mocks.move }, archives: { thumbnailUrl: () => "" }, tasks: {} } }))
vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} })
afterEach(cleanup)
beforeEach(() => { vi.clearAllMocks(); mocks.move.mockImplementation(async (keys: string[]) => ({ library_revision: 1, items: keys.map((content_key) => ({ content_key, outcome: "moved" })) })) })
function transfer() {
  const values = new Map<string, string>()
  return { effectAllowed: "", dropEffect: "", get types() { return [...values.keys()] },
    setData: (type: string, value: string) => values.set(type, value), getData: (type: string) => values.get(type) || "" }
}
describe("file organization", () => {
  it("renders the server's mixed order and moves a checked selection from the direct toolbar", async () => {
    const { container } = render(<FilesPage filters={DEFAULT_LIBRARY_FILTERS} folders={folders} />)
    const grid = screen.getByTestId("archive-grid")
    expect([...grid.children].map((child) => child.querySelector<HTMLElement>("[data-archive-path]")?.dataset.archivePath || child.querySelector<HTMLElement>("[data-folder-id]")?.dataset.folderId)).toEqual(["a", "target", "b"])
    fireEvent.click(screen.getByRole("button", { name: "选择" }))
    fireEvent.click(container.querySelector('[data-archive-path="a"]')!)
    fireEvent.click(screen.getByRole("button", { name: "移到文件夹…" }))
    fireEvent.change(screen.getByLabelText("目标位置"), { target: { value: "target" } })
    fireEvent.click(screen.getByRole("button", { name: "移动" }))
    await waitFor(() => expect(mocks.move).toHaveBeenCalledWith(["a"], "target"))
    await waitFor(() => expect(screen.getByText("已选 0 项")).toBeTruthy())
  })
  it("drags all selected contents into a folder and retains the failed selection", async () => {
    mocks.move.mockResolvedValue({ library_revision: 1, items: [{ content_key: "a", outcome: "moved" }, { content_key: "b", outcome: "failed", reason: "content_deleting" }] })
    const { container } = render(<FilesPage filters={DEFAULT_LIBRARY_FILTERS} folders={folders} />)
    fireEvent.click(screen.getByRole("button", { name: "选择" }))
    for (const id of ["a", "b"]) fireEvent.click(container.querySelector(`[data-archive-path="${id}"]`)!)
    const dataTransfer = transfer()
    fireEvent.dragStart(container.querySelector('[data-archive-path="b"]')!, { dataTransfer })
    const target = container.querySelector('[data-drop-folder="课程"]')!
    fireEvent.dragOver(target, { dataTransfer })
    expect(target.getAttribute("data-drag-over")).toBe("true")
    fireEvent.drop(target, { dataTransfer })
    await waitFor(() => expect(mocks.move).toHaveBeenCalledWith(["a", "b"], "target"))
    await waitFor(() => expect(screen.getByText("已选 1 项")).toBeTruthy())
    expect(mocks.error).toHaveBeenCalled()
  })
  it("moves contents out directly and ignores desktop file drops", async () => {
    const { container } = render(<FilesPage filters={DEFAULT_LIBRARY_FILTERS} folderId="target" folders={folders} />)
    fireEvent.click(screen.getByRole("button", { name: "选择" }))
    fireEvent.click(container.querySelector('[data-archive-path="a"]')!)
    fireEvent.click(screen.getByRole("button", { name: "移到未分类" }))
    await waitFor(() => expect(mocks.move).toHaveBeenCalledWith(["a"], null))
    mocks.move.mockClear()
    fireEvent.drop(container.querySelector('[data-drop-folder="课程"]')!, { dataTransfer: { types: ["Files"] } })
    expect(mocks.move).not.toHaveBeenCalled()
  })
})
