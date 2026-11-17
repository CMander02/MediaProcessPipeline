// @vitest-environment jsdom
import { useRef } from "react"
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { useArchivePage } from "./use-archive-page"
import { useListPosition } from "./use-view-position"
import type { ArchiveItem, ArchivePage, ArchiveQuery } from "@/repositories/archive-types"

const mocks = vi.hoisted(() => ({ listPage: vi.fn(), triggerSync: vi.fn(), server: "",
  platform: { kind: "web", isNative: false } }))
vi.mock("@/platform/use-platform", () => ({ usePlatform: () => mocks.platform }))
vi.mock("@/repositories/archive-repository", () => ({ createArchiveRepository: () => mocks }))
vi.mock("@/hooks/use-archives", () => ({ preloadThumbnails: vi.fn() }))
vi.mock("@/lib/api", () => ({ getApiClientConfig: () => ({ baseUrl: mocks.server }) }))

const query: ArchiveQuery = { page: 1, page_size: 24, search: "", media: "all", source: "all", sort: "created_desc" }
const items: ArchiveItem[] = Array.from({ length: 620 }, (_, index) => ({ archive_id: String(index), path: `/archive/${index}`,
  title: `Archive ${index}`, date: "2026-11-17", created_at: "2026-11-17", metadata: {}, analysis: {},
  has_transcript: true, has_summary: true, has_mindmap: false, has_video: false, has_audio: false, has_image: false,
  media_file: null, duration_seconds: null }))
function page(value: ArchiveQuery): ArchivePage {
  const start = value.offset ?? (value.page - 1) * value.page_size
  return { archives: items.slice(start, start + value.page_size), total: items.length, page: value.page,
    page_size: value.page_size, workspace_id: "workspace", revision: 1, indexing: false, last_reconciled_at: null }
}
let session = 0
beforeEach(() => {
  vi.clearAllMocks()
  mocks.server = `http://localhost:18000/infinite-${++session}`
  mocks.listPage.mockImplementation(async (value: ArchiveQuery) => page(value))
})
afterEach(cleanup)

describe("incremental archive browsing", () => {
  it.each([24, 40])("requests only the next fixed batch of %i entries", async (page_size) => {
    const hook = renderHook(() => useArchivePage({ ...query, page_size }, "infinite"))
    await waitFor(() => expect(hook.result.current.archives).toHaveLength(page_size))
    await act(() => hook.result.current.loadMore())
    await act(() => hook.result.current.loadMore())
    expect(hook.result.current.archives).toHaveLength(page_size * 3)
    expect(mocks.listPage.mock.calls.map(([value]) => [value.page, value.offset, value.page_size]))
      .toEqual([[1, 0, page_size], [2, page_size, page_size], [3, page_size * 2, page_size]])
  })

  it("loads all 620 entries beyond the single-request limit", async () => {
    const hook = renderHook(() => useArchivePage(query, "phone-grid"))
    await waitFor(() => expect(hook.result.current.archives).toHaveLength(24))
    for (let index = 1; index < 26; index++) await act(() => hook.result.current.loadMore())
    expect(hook.result.current.archives).toHaveLength(620)
    expect(hook.result.current.hasMore).toBe(false)
    expect(mocks.listPage).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 600, page: 26, page_size: 24 }))
    await act(() => hook.result.current.loadMore())
    expect(mocks.listPage).toHaveBeenCalledTimes(26)
  })

  it("deduplicates stable archive keys and retains mixed folder order across pages", async () => {
    const folder = { id: "folder", name: "Folder", count: 1, source_collection_key: null, created_at: "", updated_at: "" }
    mocks.listPage.mockImplementation(async (value: ArchiveQuery): Promise<ArchivePage> => ({ ...page(value), total: 4,
      archives: value.page === 1 ? [items[0]] : [items[0], items[1]],
      folders: value.page === 1 ? [folder] : [],
      entries: value.page === 1 ? [{ kind: "folder", id: "folder" }, { kind: "archive", id: "0" }]
        : [{ kind: "archive", id: "0" }, { kind: "archive", id: "1" }] }))
    const hook = renderHook(() => useArchivePage({ ...query, page_size: 2, include_folders: true }, "phone-grid"))
    await waitFor(() => expect(hook.result.current.folders).toHaveLength(1))
    await act(() => hook.result.current.loadMore())
    expect(hook.result.current.archives.map((item) => item.archive_id)).toEqual(["0", "1"])
    expect(hook.result.current.entries).toEqual([{ kind: "folder", id: "folder" }, { kind: "archive", id: "0" }, { kind: "archive", id: "1" }])
    expect(mocks.listPage).toHaveBeenLastCalledWith(expect.objectContaining({ include_folders: true, offset: 2 }))
  })

  it("refreshes the loaded range atomically and retains the list after a failed refresh", async () => {
    const hook = renderHook(() => useArchivePage(query, "phone-grid"))
    await waitFor(() => expect(hook.result.current.archives).toHaveLength(24))
    await act(() => hook.result.current.loadMore())
    await act(() => hook.result.current.loadMore())
    const before = hook.result.current.archives
    const pending: Array<() => void> = []
    mocks.listPage.mockImplementation((value: ArchiveQuery) => new Promise<ArchivePage>((resolve) => {
      pending.push(() => resolve({ ...page(value), archives: page(value).archives.map((item) => ({ ...item, title: `Updated ${item.title}` })) }))
    }))
    let refresh: Promise<void>
    act(() => { refresh = hook.result.current.refresh(true) })
    expect(hook.result.current.archives).toBe(before)
    expect(hook.result.current.loading).toBe(false)
    await act(async () => { pending.forEach((resolve) => resolve()); await refresh })
    expect(hook.result.current.archives).toHaveLength(72)
    expect(hook.result.current.archives[0].title).toBe("Updated Archive 0")
    expect(mocks.listPage.mock.calls.slice(-3).map(([value]) => value.offset)).toEqual([0, 24, 48])
    mocks.listPage.mockRejectedValue(new Error("Offline"))
    await act(() => hook.result.current.refresh(true))
    expect(hook.result.current.archives).toHaveLength(72)
    expect(hook.result.current.error).toContain("Offline")
  })

  it("restores loaded pages and exact scroll position before the return refresh completes", async () => {
    function List() {
      const list = useArchivePage(query, "phone-grid")
      const ref = useRef<HTMLDivElement>(null)
      useListPosition(list.sessionKey, ref, !list.loading)
      return <div ref={ref} data-testid="list"><output>{list.archives.length}</output>
        <button onClick={() => void list.loadMore()}>More</button></div>
    }
    const first = render(<List />)
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("24"))
    fireEvent.click(screen.getByText("More"))
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("48"))
    fireEvent.click(screen.getByText("More"))
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("72"))
    fireEvent.scroll(screen.getByTestId("list"), { target: { scrollTop: 1275 } })
    first.unmount()
    mocks.listPage.mockReturnValue(new Promise<ArchivePage>(() => {}))
    render(<List />)
    expect(screen.getByRole("status").textContent).toBe("72")
    expect(screen.getByTestId("list").scrollTop).toBe(1275)
  })

  it("replays a silent refresh received while the next page is loading", async () => {
    const hook = renderHook(() => useArchivePage(query, "phone-grid"))
    await waitFor(() => expect(hook.result.current.archives).toHaveLength(24))
    let finish: () => void = () => {}
    mocks.listPage.mockImplementationOnce((value: ArchiveQuery) => new Promise<ArchivePage>((resolve) => {
      finish = () => resolve(page(value))
    }))
    act(() => { void hook.result.current.loadMore() })
    act(() => { window.dispatchEvent(new Event("mpp:library-refresh")) })
    await act(async () => finish())
    await waitFor(() => expect(mocks.listPage).toHaveBeenCalledTimes(4))
    expect(mocks.listPage.mock.calls.map(([value]) => value.offset)).toEqual([0, 24, 0, 24])
    expect(hook.result.current.archives).toHaveLength(48)
  })

  it("ignores an old page after changing the query", async () => {
    let finish: (value: ArchivePage) => void = () => {}
    const hook = renderHook((search: string) => useArchivePage({ ...query, search }, "phone-grid"), { initialProps: "" })
    await waitFor(() => expect(hook.result.current.archives).toHaveLength(24))
    mocks.listPage.mockImplementation((value: ArchiveQuery) => value.search === ""
      ? new Promise<ArchivePage>((resolve) => { finish = resolve })
      : Promise.resolve({ ...page(value), archives: [items[100]] }))
    act(() => { void hook.result.current.loadMore() })
    hook.rerender("new search")
    await waitFor(() => expect(hook.result.current.archives[0].archive_id).toBe("100"))
    await act(async () => finish(page({ ...query, page: 2 })))
    expect(hook.result.current.archives.map((item) => item.archive_id)).toEqual(["100"])
  })
})
