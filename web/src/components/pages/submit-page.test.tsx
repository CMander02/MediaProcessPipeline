/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import "@testing-library/jest-dom/vitest"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { SubmitPage } from "./submit-page"
import { DEFAULT_WORKFLOW } from "@/lib/workflow"
import { api, type BilibiliCollectionResult, type Task } from "@/lib/api"
import { navigate } from "@/lib/router"
import { notifySuccess } from "@/lib/notify"

vi.mock("@/lib/router", () => ({ navigate: vi.fn() }))
vi.mock("@/hooks/use-library-directory", () => ({ useLibraryDirectory: () => ({ folders: [] }), libraryChanged: vi.fn() }))
vi.mock("@/lib/notify", () => ({ notifySuccess: vi.fn() }))
vi.mock("@/hooks/use-active-tasks", () => ({ useActiveTasks: () => ({ tasks: [], loaded: true }) }))
vi.mock("@/hooks/use-app-access-context", () => ({ useAppAccess: () => ({
  online: true, authExpired: false,
  capabilities: { mode: "local", authenticated: true, url_submission: true, browser_file_upload: true,
    browser_folder_upload: true, task_control: true, settings: true, filesystem_browse: true,
    local_path_submission: true, open_local_folder: true, archive_mutation: true },
}) }))

const linkBox = () => screen.getByPlaceholderText(/粘贴视频或网页链接/)
const enter = (value: string) => fireEvent.change(linkBox(), { target: { value } })
const submit = async () => {
  const button = screen.getByRole("button", { name: /^开始处理/ })
  await waitFor(() => expect(button).not.toBeDisabled())
  fireEvent.click(button)
}
const task = (id: string) => ({ id, status: "queued" } as Task)
let fixtureNumber = 0
function collection(): BilibiliCollectionResult {
  fixtureNumber += 1
  const bvid = `BV${String(fixtureNumber).padStart(10, "0")}`
  return {
    is_bilibili: true, is_collection: true, collection_type: "ugc_season", title: "设计课程", current_item_id: `${bvid}:1`,
    items: ["第一集", "第二集", "第三集"].map((title, index) => ({
      id: `${bvid}:${index + 1}`, bvid, page: index + 1, title, duration: 300, cover: null,
      url: `https://www.bilibili.com/video/${bvid}?p=${index + 1}`,
      position: index + 1, published_at: index === 0 ? null : 1700000000,
    })),
  }
}
async function showCollection(data: BilibiliCollectionResult) {
  vi.mocked(api.pipeline.bilibiliCollection).mockResolvedValue(data)
  enter(data.items[0].url)
  await screen.findByRole("button", { name: /选择其他视频/ })
}
const openCollection = () => fireEvent.click(screen.getByRole("button", { name: /选择其他视频/ }))

beforeEach(() => {
  vi.spyOn(api.settings, "get").mockResolvedValue({} as never)
  vi.spyOn(api.tasks, "list").mockResolvedValue([])
  vi.spyOn(api.archives, "lookup").mockResolvedValue({ matches: {} })
  vi.spyOn(api.pipeline, "probe").mockRejectedValue(new Error("offline"))
  vi.spyOn(api.pipeline, "bilibiliCollection").mockResolvedValue({ is_bilibili: true, is_collection: false, items: [] })
  vi.spyOn(api.tasks, "importSources").mockImplementation(async (sources) => ({ items: sources.map((source, index) => ({
    source, content_key: source, outcome: "created", task: task(`task-${index}`),
  })) }))
})
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.mocked(navigate).mockReset(); vi.mocked(notifySuccess).mockReset() })

describe("collection selection", () => {
  it("inherits global workflow, keeps ASR required and applies temporary overrides only to this submission", async () => {
    vi.mocked(api.settings.get).mockResolvedValue({ pipeline_workflow: { ...DEFAULT_WORKFLOW, separate: false } } as never)
    const patch = vi.spyOn(api.settings, "patch")
    render(<SubmitPage />)
    enter("https://example.com/workflow")
    fireEvent.click(screen.getByText(/处理流程 ·/))
    await waitFor(() => expect(screen.getByRole("checkbox", { name: "人声分离" })).not.toBeChecked())
    expect(screen.getByRole("checkbox", { name: /ASR 语音识别/ })).toBeDisabled()
    fireEvent.click(screen.getByRole("checkbox", { name: "字幕润色" }))
    await submit()
    await waitFor(() => expect(api.tasks.importSources).toHaveBeenCalledWith(["https://example.com/workflow"], {
      force_asr: true, workflow: { ...DEFAULT_WORKFLOW, separate: false, polish: false },
    }))
    expect(patch).not.toHaveBeenCalled()
  })

  it("submits only the current video without opening the selector", async () => {
    render(<SubmitPage />)
    const data = collection()
    await showCollection(data)
    await submit()
    await waitFor(() => expect(api.tasks.importSources).toHaveBeenCalledWith([data.items[0].url], { force_asr: true, workflow: DEFAULT_WORKFLOW }))
    expect(navigate).toHaveBeenCalledWith("#/result/task/task-0")
  })

  it("preserves selection through search and sorting; unknown dates sort last", async () => {
    render(<SubmitPage />)
    const data = collection()
    await showCollection(data)
    openCollection()
    expect(screen.getByLabelText("选择 第一集")).toBeChecked()
    expect(screen.getByLabelText("选择 第二集")).not.toBeChecked()
    fireEvent.click(screen.getByLabelText("选择 第二集"))
    fireEvent.change(screen.getByRole("combobox", { name: "排序" }), { target: { value: "newest" } })
    expect(within(screen.getByRole("list", { name: "合集视频" })).getAllByRole("checkbox").map((item) => item.getAttribute("aria-label")))
      .toEqual(["选择 第二集", "选择 第三集", "选择 第一集"])
    fireEvent.change(screen.getByLabelText("搜索视频标题"), { target: { value: "第三" } })
    expect(screen.getByText("已选 2 项")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "返回提交" }))
    expect(api.tasks.importSources).not.toHaveBeenCalled()
    await submit()
    await waitFor(() => expect(api.tasks.importSources).toHaveBeenCalledWith(data.items.slice(0, 2).map((item) => item.url), { force_asr: true, workflow: DEFAULT_WORKFLOW }))
  })

  it("keeps other members available when the pasted video is already in the library", async () => {
    const data = collection()
    vi.mocked(api.archives.lookup).mockResolvedValue({ matches: { [data.items[0].url]: [{ path: "archive", title: "第一集", task_id: "old", status: "completed", processing: false }] } })
    render(<SubmitPage />)
    await showCollection(data)
    openCollection()
    await screen.findByText("已在库中")
    await waitFor(() => expect(screen.getAllByText("未导入")).toHaveLength(2))
    fireEvent.click(screen.getByRole("button", { name: "只选未导入" }))
    expect(screen.getByLabelText("选择 第一集")).not.toBeChecked()
    expect(screen.getByLabelText("选择 第二集")).toBeChecked()
    fireEvent.click(screen.getByRole("button", { name: "返回提交" }))
    await submit()
    await waitFor(() => expect(api.tasks.importSources).toHaveBeenCalledWith(data.items.slice(1).map((item) => item.url), { force_asr: true, workflow: DEFAULT_WORKFLOW }))
  })

  it("refreshes the directory and keeps only still-present selections", async () => {
    const data = collection()
    render(<SubmitPage />)
    await showCollection(data)
    openCollection()
    fireEvent.click(screen.getByLabelText("选择 第二集"))
    const added = { ...data.items[2], id: "new", title: "新增视频", url: "https://example.com/new" }
    vi.mocked(api.pipeline.bilibiliCollection).mockResolvedValue({ ...data, items: [data.items[1], added] })
    fireEvent.click(screen.getByRole("button", { name: "刷新目录" }))
    await screen.findByText("新增视频")
    expect(screen.getByLabelText("选择 第二集")).toBeChecked()
    expect(screen.getByLabelText("选择 新增视频")).not.toBeChecked()
    expect(screen.getByText(/1 个已选视频已不在目录中/)).toBeInTheDocument()
  })

  it("keeps lookup failures unknown and excludes them from only-unimported selection", async () => {
    vi.mocked(api.archives.lookup).mockRejectedValue(new Error("offline"))
    render(<SubmitPage />)
    await showCollection(collection())
    openCollection()
    expect(screen.getAllByText("状态未确认")).toHaveLength(3)
    fireEvent.click(screen.getByRole("button", { name: "只选未导入" }))
    expect(screen.getByText("已选 0 项")).toBeInTheDocument()
  })

  it("falls back to only the pasted video when the current member is missing", async () => {
    const data = collection()
    data.current_item_id = "missing"
    render(<SubmitPage />)
    await showCollection(data)
    await submit()
    await waitFor(() => expect(api.tasks.importSources).toHaveBeenCalledWith([data.items[0].url], { force_asr: true, workflow: DEFAULT_WORKFLOW }))
  })

  it("retries a failed directory request instead of caching the failure", async () => {
    const data = collection()
    vi.mocked(api.pipeline.bilibiliCollection).mockRejectedValueOnce(new Error("offline")).mockResolvedValue(data)
    render(<SubmitPage />)
    enter(data.items[0].url)
    fireEvent.click(await screen.findByRole("button", { name: "重新读取目录" }))
    await screen.findByRole("button", { name: /选择其他视频/ })
    openCollection()
    expect(screen.getByLabelText("选择 第一集")).toBeChecked()
    expect(screen.getByLabelText("选择 第二集")).not.toBeChecked()
  })

  it("checks a 201-item directory in batches of at most 200", async () => {
    const data = collection()
    data.items = Array.from({ length: 201 }, (_, index) => ({ ...data.items[0], id: `member-${index}`, url: `https://example.com/member/${index}` }))
    data.current_item_id = data.items[0].id
    vi.mocked(api.pipeline.bilibiliCollection).mockResolvedValue(data)
    render(<SubmitPage />)
    enter("https://www.bilibili.com/video/BV0000000201")
    await waitFor(() => expect(vi.mocked(api.archives.lookup).mock.calls.some(([sources]) => sources.length === 200)).toBe(true), { timeout: 2000 })
    expect(vi.mocked(api.archives.lookup).mock.calls.every(([sources]) => sources.length <= 200)).toBe(true)
  })

  it("labels incomplete directories and selects only loaded members", async () => {
    const data = collection()
    data.complete = false
    data.total = 10
    render(<SubmitPage />)
    await showCollection(data)
    openCollection()
    expect(screen.getByText(/已加载 3\/共 10 项/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "全选已加载" }))
    expect(screen.getByText("已选 3 项")).toBeInTheDocument()
  })
})

describe("submission", () => {
  it("creates a collection folder only for the currently submitted collection", async () => {
    render(<SubmitPage />)
    const data = collection()
    await showCollection(data)
    fireEvent.click(screen.getByLabelText(`以「${data.title}」创建文件夹`))
    await submit()
    await waitFor(() => expect(api.tasks.importSources).toHaveBeenCalledWith([data.items[0].url], { force_asr: true, workflow: DEFAULT_WORKFLOW }, {
      collection_folders: [{ source: data.items[0].url, create_folder: true }],
    }))
  })

  it("sends an explicit unclassified choice separately from automatic collection assignment", async () => {
    render(<SubmitPage />)
    enter("https://example.com/without-folder")
    fireEvent.change(screen.getByLabelText("导入到文件夹"), { target: { value: "none" } })
    await submit()
    await waitFor(() => expect(api.tasks.importSources).toHaveBeenCalledWith(["https://example.com/without-folder"], { force_asr: true, workflow: DEFAULT_WORKFLOW }, { folder_id: null }))
  })
  it("uses explicit ASR options for ordinary pasted links", async () => {
    render(<SubmitPage />)
    enter("https://youtu.be/aaaaaaaaaaa\nhttps://example.com/video")
    await submit()
    await waitFor(() => expect(api.tasks.importSources).toHaveBeenCalledWith(["https://youtu.be/aaaaaaaaaaa", "https://example.com/video"], { force_asr: true, workflow: DEFAULT_WORKFLOW }))
    expect(navigate).not.toHaveBeenCalled()
  })

  it("reports reused inputs without claiming they entered the queue", async () => {
    vi.mocked(api.tasks.importSources).mockImplementation(async (sources) => ({ items: sources.map((source) => ({ source, content_key: source, outcome: "reused", match: { path: "done", title: "已有视频", status: "completed", processing: false } })) }))
    render(<SubmitPage />)
    enter("https://example.com/done")
    await submit()
    await waitFor(() => expect(notifySuccess).toHaveBeenCalledWith("新增 0 项，复用 1 项，处理中 0 项"))
    expect(navigate).not.toHaveBeenCalled()
  })

  it("batches 101 inputs, then retries only the unresolved input", async () => {
    const sources = Array.from({ length: 101 }, (_, index) => `https://example.com/video/${index}`)
    vi.mocked(api.tasks.importSources).mockImplementationOnce(async (batch) => ({ items: batch.map((source, index) => ({ source, content_key: source, outcome: "created", task: task(String(index)) })) }))
      .mockRejectedValueOnce(new Error("网络中断"))
    render(<SubmitPage />)
    enter(sources.join("\n"))
    expect(screen.getByText(/将分 2 批提交/)).toBeInTheDocument()
    await submit()
    await screen.findByText(/新增 100 项/)
    expect(api.tasks.importSources).toHaveBeenNthCalledWith(1, sources.slice(0, 100), { force_asr: true, workflow: DEFAULT_WORKFLOW })
    expect(api.tasks.importSources).toHaveBeenNthCalledWith(2, sources.slice(100), { force_asr: true, workflow: DEFAULT_WORKFLOW })
    expect(navigate).not.toHaveBeenCalled()
    await submit()
    await waitFor(() => expect(api.tasks.importSources).toHaveBeenNthCalledWith(3, sources.slice(100), { force_asr: true, workflow: DEFAULT_WORKFLOW }))
  })

  it("uses full rerun for a single explicitly requested video", async () => {
    const source = "https://example.com/rerun"
    vi.mocked(api.archives.lookup).mockResolvedValue({ matches: { [source]: [{ path: "done", title: "旧视频", task_id: "old", status: "completed", processing: false }] } })
    const rerun = vi.spyOn(api.tasks, "fullRerun").mockResolvedValue({ message: "ok" })
    render(<SubmitPage />)
    enter(source)
    fireEvent.click(await screen.findByRole("button", { name: "仍然重新处理" }))
    await waitFor(() => expect(rerun).toHaveBeenCalledWith("old"))
    expect(api.tasks.importSources).not.toHaveBeenCalled()
  })

  it("keeps historical failures for an explicit user action", async () => {
    vi.mocked(api.tasks.importSources).mockImplementation(async (sources) => ({ items: sources.map((source) => ({ source, content_key: source, outcome: "skipped", reason: "needs_action", match: { path: "failed", task_id: "old-failed", title: "历史失败", status: "failed", processing: false } })) }))
    const rerun = vi.spyOn(api.tasks, "fullRerun")
    render(<SubmitPage />)
    enter("https://example.com/failed")
    await submit()
    await screen.findByText(/待操作 1 项/)
    expect(linkBox()).toHaveValue("https://example.com/failed")
    expect(rerun).not.toHaveBeenCalled()
    expect(navigate).not.toHaveBeenCalled()
  })

  it("keeps uploaded files on partial failure and supports local paths", async () => {
    const createElement = document.createElement.bind(document)
    vi.spyOn(document, "createElement").mockImplementation((name, options) => {
      const element = createElement(name, options)
      if (name === "video") queueMicrotask(() => element.dispatchEvent(new Event("error")))
      return element
    })
    vi.spyOn(api.pipeline, "stage").mockResolvedValue({ staging_id: "upload", path: "C:/staging/video.mp4", filename: "video.mp4", title: "video", size: 5, media_type: "video" })
    vi.mocked(api.tasks.importSources).mockImplementation(async (sources) => ({ items: sources.map((source) => ({ source, content_key: null, outcome: "failed", error: "暂时失败" })) }))
    const { container } = render(<SubmitPage />)
    enter("C:/media/local.mp3")
    fireEvent.change(container.querySelector('input[type="file"]')!, { target: { files: [new File(["video"], "video.mp4", { type: "video/mp4" })] } })
    await waitFor(() => expect(screen.queryByText("上传中…")).not.toBeInTheDocument())
    await submit()
    await screen.findByText(/新增 0 项 · 复用 0 项/)
    expect(api.tasks.importSources).toHaveBeenCalledWith(["C:/media/local.mp3", "C:/staging/video.mp4"], { force_asr: true, workflow: DEFAULT_WORKFLOW })
    expect(screen.getByText("video.mp4")).toBeInTheDocument()
    expect(linkBox()).toHaveValue("C:/media/local.mp3")
  })
})
