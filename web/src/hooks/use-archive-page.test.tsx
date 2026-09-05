// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { useArchivePage } from "@/hooks/use-archive-page"
import type { ArchiveItem, ArchivePage, ArchiveQuery } from "@/repositories/archive-types"

const mocks = vi.hoisted(() => ({ listPage: vi.fn(), triggerSync: vi.fn(), server: "",
  platform: { kind: "web", isNative: false } }))
vi.mock("@/platform/use-platform", () => ({ usePlatform: () => mocks.platform }))
vi.mock("@/repositories/archive-repository", () => ({ createArchiveRepository: () => mocks }))
vi.mock("@/hooks/use-archives", () => ({ preloadThumbnails: vi.fn() }))
vi.mock("@/lib/api", () => ({ getApiClientConfig: () => ({ baseUrl: mocks.server }) }))

const query: ArchiveQuery = { page: 1, page_size: 28, search: "", media: "all", source: "all", sort: "created_desc" }
let serverNumber = 0
function result(page = 1, workspace = "root-a"): ArchivePage {
  return { archives: [{ path: `${workspace}/${page}`, title: `Page ${page}` } as ArchiveItem],
    total: 56, page, page_size: 28, workspace_id: workspace, indexing: false,
    last_reconciled_at: null, revision: 1 }
}
beforeEach(() => {
  vi.clearAllMocks()
  mocks.server = `http://localhost:18000/test-${++serverNumber}`
})
afterEach(cleanup)

describe("archive pagination requests", () => {
  it("shares an in-flight page request between subscribers", async () => {
    let resolve!: (page: ArchivePage) => void
    mocks.listPage.mockReturnValue(new Promise<ArchivePage>((done) => { resolve = done }))
    const first = renderHook(() => useArchivePage(query))
    const second = renderHook(() => useArchivePage(query))
    expect(mocks.listPage).toHaveBeenCalledTimes(1)
    await act(async () => resolve(result()))
    expect(first.result.current.archives[0].title).toBe("Page 1")
    expect(second.result.current.archives[0].title).toBe("Page 1")
  })

  it("ignores a late response after the user changes pages", async () => {
    let first!: (page: ArchivePage) => void
    mocks.listPage.mockImplementation((value: ArchiveQuery) => value.page === 1
      ? new Promise<ArchivePage>((resolve) => { first = resolve }) : Promise.resolve(result(2)))
    const hook = renderHook((value: ArchiveQuery) => useArchivePage(value), { initialProps: query })
    hook.rerender({ ...query, page: 2 })
    await waitFor(() => expect(hook.result.current.archives[0]?.title).toBe("Page 2"))
    await act(async () => first(result(1)))
    expect(hook.result.current.archives[0].title).toBe("Page 2")
  })

  it("separates workspace and page-size caches", async () => {
    mocks.listPage.mockResolvedValue(result())
    const hook = renderHook((value: ArchiveQuery) => useArchivePage(value), { initialProps: query })
    await waitFor(() => expect(hook.result.current.loading).toBe(false))
    hook.rerender({ ...query, page_size: 14 })
    await waitFor(() => expect(mocks.listPage).toHaveBeenCalledWith({ ...query, page_size: 14 }))
    mocks.listPage.mockResolvedValue(result(1, "root-b"))
    act(() => window.dispatchEvent(new CustomEvent("mpp:workspace-change", { detail: { workspace_id: "root-b" } })))
    await waitFor(() => expect(hook.result.current.archives[0]?.path).toBe("root-b/1"))
    expect(mocks.listPage).toHaveBeenCalledTimes(3)
  })

  it("refreshes totals and accepts a clamped page after deleting the last item", async () => {
    mocks.listPage.mockResolvedValue(result(2))
    const hook = renderHook(() => useArchivePage({ ...query, page: 2 }))
    await waitFor(() => expect(hook.result.current.loading).toBe(false))
    expect(hook.result.current.page).toBe(2)
    mocks.listPage.mockResolvedValue({ ...result(1), total: 28 })
    act(() => hook.result.current.removeArchive())
    await waitFor(() => expect(hook.result.current.page).toBe(1))
    expect(hook.result.current.total).toBe(28)
  })
})
