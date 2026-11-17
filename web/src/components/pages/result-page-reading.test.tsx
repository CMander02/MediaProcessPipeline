// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { ResultPageComplete } from "./result-page-complete"
import type { ArchiveItem } from "@/repositories/archive-types"

const mocks = vi.hoisted(() => ({
  get: vi.fn(), readFile: vi.fn(), refresh: vi.fn(), updatePrefs: vi.fn(), phone: false,
  platform: { kind: "web", isNative: false }, archives: [],
}))
const archive: ArchiveItem = {
  archive_id: "text", path: "/archives/text", title: "Text archive", date: "2026-11-17", created_at: "2026-11-17",
  has_video: true, has_audio: false, has_image: false, has_transcript: true, has_summary: true, has_mindmap: false,
  media_file: null, metadata: { source_url: "https://example.com/source" }, analysis: {}, duration_seconds: 30,
}
vi.mock("@/platform/use-platform", () => ({ usePlatform: () => mocks.platform }))
vi.mock("@/repositories/archive-repository", () => ({ createArchiveRepository: () => ({ get: mocks.get, readFile: mocks.readFile, listPage: async () => ({ archives: [], total: 0, workspace_id: "reading" }) }) }))
vi.mock("@/hooks/use-app-access-context", () => ({ useAppAccess: () => ({ capabilities: {}, online: true }) }))
vi.mock("@/hooks/use-archives", () => ({ useArchives: () => ({ archives: mocks.archives, refresh: mocks.refresh }) }))
vi.mock("@/hooks/use-preferences", () => ({ usePreferences: () => ({ prefs: {}, update: mocks.updatePrefs }) }))
vi.mock("@/hooks/use-task-sse", () => ({ useTaskSSE: vi.fn() }))
vi.mock("@/components/result/attempts-menu", () => ({ AttemptsMenu: () => null }))
vi.mock("@/lib/api", () => ({ getApiClientConfig: () => ({ baseUrl: "http://localhost" }), api: { archives: { thumbnailUrl: () => "" } } }))

beforeEach(() => {
  vi.clearAllMocks()
  mocks.phone = false
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(new DOMRect(0, 0, 1440, 900))
  mocks.get.mockResolvedValue(archive)
  mocks.readFile.mockImplementation(async (path: string) => path.endsWith("summary.md") ? "Summary text" : "")
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: mocks.phone && query.includes("max-width: 767px"), addEventListener: vi.fn(), removeEventListener: vi.fn(),
  }))
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} })
})
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe("text-only desktop reading", () => {
  it("maximizes reading by default and toggles the source sidebar", async () => {
    const view = render(<ResultPageComplete archivePath={archive.path} taskId={null} />)
    expect(await screen.findByTestId("result-source-bar")).toBeTruthy()
    expect(await screen.findByText("Summary text")).toBeTruthy()
    expect(screen.getByTestId("result-reading-workspace")).toBeTruthy()
    expect(view.container.querySelector('[data-slot="resizable-panel-group"]')).toBeNull()
    expect(screen.getByRole("link", { name: "打开原始链接" }).getAttribute("href")).toBe("https://example.com/source")
    fireEvent.click(screen.getByRole("button", { name: "展开侧栏" }))
    expect(view.container.querySelector('[data-slot="resizable-panel-group"]')).toBeTruthy()
    expect(screen.getByRole("button", { name: "折叠侧栏" }).getAttribute("aria-expanded")).toBe("true")
    fireEvent.click(screen.getByRole("button", { name: "折叠侧栏" }))
    expect(screen.getByTestId("result-reading-workspace")).toBeTruthy()
  })

  it("uses one title editor when the embedded source sidebar is expanded", async () => {
    render(<ResultPageComplete embedded archivePath={archive.path} taskId={null} />)
    await screen.findByTestId("result-source-bar")
    fireEvent.click(screen.getByRole("button", { name: "展开侧栏" }))
    fireEvent.click(screen.getByTitle("点击编辑标题"))
    expect(screen.getAllByRole("textbox")).toHaveLength(1)
  })

  it("keeps the mobile result layout without desktop source controls", async () => {
    mocks.phone = true
    const view = render(<ResultPageComplete archivePath={archive.path} taskId={null} />)
    await screen.findByText("Summary text")
    expect(screen.queryByTestId("result-source-bar")).toBeNull()
    expect(screen.queryByRole("button", { name: "展开侧栏" })).toBeNull()
    expect(view.container.querySelector("[data-mobile-player]")).toBeTruthy()
  })
})
