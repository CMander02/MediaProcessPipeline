/** @vitest-environment jsdom */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { MobileMediaOverview } from "./mobile-media-overview"
import { libraryHash, navigate, parseHash, previousHash } from "@/lib/router"
import type { useResultViewer } from "@/hooks/use-result-viewer"
import type { ArchiveItem } from "@/repositories/archive-types"

const mocks = vi.hoisted(() => ({ query: vi.fn() }))
const current: ArchiveItem = { path: "/a", content_key: "a", title: "当前视频", has_video: true, has_audio: false, has_image: false, has_transcript: true, has_summary: true, has_mindmap: false, media_file: null, duration_seconds: 60, analysis: {}, created_at: "2026-10-19", metadata: { description: "展开后的视频说明" }, date: "2026-10-19" }
const items = [current, { ...current, path: "/old-a" }, { ...current, path: "/b", content_key: "b", title: "下一个视频" }]
vi.mock("@/hooks/use-archive-page", () => ({ useArchivePage: (query: unknown) => { mocks.query(query); return { archives: items, total: 3, loading: false } } }))
vi.mock("@/lib/api", () => ({ api: { archives: { thumbnailUrl: () => "/thumbnail.jpg" } } }))
vi.mock("./summary-tab", () => ({ SummaryTab: () => <p>完整摘要</p> }))
const view = { archive: current, displayTitle: "当前视频", mediaType: "video", duration: 60, summary: "摘要", handleOpenSource: vi.fn() } as unknown as ReturnType<typeof useResultViewer>
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe("mobile media overview", () => {
  it("expands the description and opens another item without losing the source grid", () => {
    act(() => navigate("#/files?q=course&scope=all&page=3"))
    const source = libraryHash()
    act(() => navigate("#/result/archive?path=%2Fa"))
    render(<MobileMediaOverview view={view} actions={null} />)
    expect(screen.queryByText("展开后的视频说明")).toBeNull()
    fireEvent.click(screen.getByRole("button", { name: "展开简介" }))
    expect(screen.getByText("展开后的视频说明")).toBeTruthy()
    expect(screen.getAllByRole("button", { name: /下一个视频/ })).toHaveLength(1)
    expect(screen.queryByRole("button", { name: /当前视频/ })).toBeNull()
    fireEvent.click(screen.getByRole("button", { name: /下一个视频/ }))
    expect(parseHash(window.location.hash)).toMatchObject({ page: "result", resultId: "/b", params: { play: "1" } })
    expect(previousHash()).toBe(source)
    expect(libraryHash()).toBe(source)
    expect(mocks.query).toHaveBeenLastCalledWith(expect.objectContaining({ search: "course", media: "video,audio" }))
  })

  it("changes an embedded item within the current folder and clears its old processing version", () => {
    act(() => navigate("#/files?view=split&folder=course&item=a&attempt=old&viewerTask=old-task&generation=1"))
    render(<MobileMediaOverview view={view} actions={null} />)
    fireEvent.click(screen.getByRole("button", { name: /下一个视频/ }))
    expect(parseHash(window.location.hash).params).toEqual({ view: "split", folder: "course", item: "b", play: "1" })
    expect(mocks.query).toHaveBeenLastCalledWith(expect.objectContaining({ folder_id: "course" }))
  })
})
