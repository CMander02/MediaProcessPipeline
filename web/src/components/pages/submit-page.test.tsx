/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import "@testing-library/jest-dom/vitest"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { SubmitPage } from "./submit-page"
import { api } from "@/lib/api"
import { navigate } from "@/lib/router"

vi.mock("@/lib/router", () => ({ navigate: vi.fn() }))
vi.mock("@/hooks/use-app-access-context", () => ({
  useAppAccess: () => ({
    online: true,
    authExpired: false,
    capabilities: {
      mode: "local",
      authenticated: true,
      url_submission: true,
      browser_file_upload: true,
      browser_folder_upload: true,
      task_control: true,
      settings: true,
      filesystem_browse: true,
      local_path_submission: true,
      open_local_folder: true,
      archive_mutation: true,
    },
  }),
}))

const linkBox = () => screen.getByPlaceholderText(/粘贴视频或网页链接/)

beforeEach(() => {
  vi.spyOn(api.archives, "lookup").mockResolvedValue({ matches: {} })
  vi.spyOn(api.pipeline, "probe").mockRejectedValue(new Error("offline"))
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.mocked(navigate).mockReset()
})

describe("SubmitPage Bilibili collection selection", () => {
  it("opens the part list and submits the selected parts as one batch", async () => {
    const inspect = vi.spyOn(api.pipeline, "bilibiliCollection").mockResolvedValue({
      is_bilibili: true,
      is_collection: true,
      collection_type: "multipart",
      title: "零基础平面设计入门系列",
      current_item_id: "BV1DK4y1b7bY:p1",
      items: [
        {
          id: "BV1DK4y1b7bY:p1",
          bvid: "BV1DK4y1b7bY",
          page: 1,
          title: "第一集 文字排版",
          duration: 384,
          cover: null,
          url: "https://www.bilibili.com/video/BV1DK4y1b7bY",
        },
        {
          id: "BV1DK4y1b7bY:p2",
          bvid: "BV1DK4y1b7bY",
          page: 2,
          title: "第二集 色彩理论",
          duration: 393,
          cover: null,
          url: "https://www.bilibili.com/video/BV1DK4y1b7bY?p=2",
        },
      ],
    })
    const createBatch = vi.spyOn(api.tasks, "createBatch").mockResolvedValue([
      { id: "task-1" },
    ] as Awaited<ReturnType<typeof api.tasks.createBatch>>)

    render(<SubmitPage />)

    fireEvent.change(linkBox(), { target: { value: "https://www.bilibili.com/video/BV1DK4y1b7bY/" } })
    fireEvent.click(screen.getByRole("button", { name: "开始处理" }))

    expect(await screen.findByText("零基础平面设计入门系列")).toBeInTheDocument()
    expect(screen.getByText("第一集 文字排版")).toBeInTheDocument()
    expect(screen.getByText("第二集 色彩理论")).toBeInTheDocument()
    expect(inspect).toHaveBeenCalledWith("https://www.bilibili.com/video/BV1DK4y1b7bY/")
    expect(createBatch).not.toHaveBeenCalled()

    fireEvent.click(screen.getByLabelText("选择 第二集 色彩理论"))
    fireEvent.click(screen.getByRole("button", { name: "开始处理" }))

    await waitFor(() => {
      expect(createBatch).toHaveBeenCalledWith(["https://www.bilibili.com/video/BV1DK4y1b7bY"], { force_asr: false })
    })
    expect(navigate).toHaveBeenCalledWith("#/result/task/task-1")
  })
})

describe("SubmitPage options", () => {
  it("submits the explicit ASR strategy", async () => {
    const createBatch = vi.spyOn(api.tasks, "createBatch").mockResolvedValue([
      { id: "task-asr" },
    ] as Awaited<ReturnType<typeof api.tasks.createBatch>>)

    render(<SubmitPage />)

    fireEvent.change(linkBox(), { target: { value: "https://www.youtube.com/watch?v=abcdefghijk" } })
    fireEvent.click(screen.getByRole("radio", { name: "强制 ASR" }))
    fireEvent.click(screen.getByRole("button", { name: "开始处理" }))

    await waitFor(() => {
      expect(createBatch).toHaveBeenCalledWith(["https://www.youtube.com/watch?v=abcdefghijk"], { force_asr: true })
    })
  })

  it("submits an explicit automatic strategy", async () => {
    const createBatch = vi.spyOn(api.tasks, "createBatch").mockResolvedValue([
      { id: "task-auto" },
    ] as Awaited<ReturnType<typeof api.tasks.createBatch>>)

    render(<SubmitPage />)

    fireEvent.change(linkBox(), { target: { value: "https://www.youtube.com/watch?v=abcdefghijk" } })
    fireEvent.click(screen.getByRole("radio", { name: "强制 ASR" }))
    fireEvent.click(screen.getByRole("radio", { name: "自动" }))
    fireEvent.click(screen.getByRole("button", { name: "开始处理" }))

    await waitFor(() => {
      expect(createBatch).toHaveBeenCalledWith(["https://www.youtube.com/watch?v=abcdefghijk"], { force_asr: false })
    })
  })
})

describe("SubmitPage several links", () => {
  it("queues every pasted link in one batch and stays on the page", async () => {
    const createBatch = vi.spyOn(api.tasks, "createBatch").mockResolvedValue([
      { id: "a" }, { id: "b" },
    ] as Awaited<ReturnType<typeof api.tasks.createBatch>>)

    render(<SubmitPage />)

    fireEvent.change(linkBox(), {
      target: { value: "【分享】 https://youtu.be/aaaaaaaaaaa 看看\nhttps://www.xiaohongshu.com/explore/123" },
    })
    fireEvent.click(screen.getByRole("button", { name: "开始处理（2 个）" }))

    await waitFor(() => {
      expect(createBatch).toHaveBeenCalledWith(
        ["https://youtu.be/aaaaaaaaaaa", "https://www.xiaohongshu.com/explore/123"],
        { force_asr: false },
      )
    })
    expect(navigate).not.toHaveBeenCalled()
  })

  it("skips links already in the library unless asked to process them again", async () => {
    vi.mocked(api.archives.lookup).mockResolvedValue({
      matches: {
        "https://youtu.be/aaaaaaaaaaa": [
          { path: "D:/MPP/archives/done", title: "已处理的视频", task_id: "t0", status: "completed", processing: false },
        ],
      },
    })
    const createBatch = vi.spyOn(api.tasks, "createBatch").mockResolvedValue([
      { id: "c" },
    ] as Awaited<ReturnType<typeof api.tasks.createBatch>>)

    render(<SubmitPage />)

    fireEvent.change(linkBox(), { target: { value: "https://youtu.be/aaaaaaaaaaa\nhttps://youtu.be/bbbbbbbbbbb" } })

    expect(await screen.findByText("已在库中")).toBeInTheDocument()
    expect(screen.getByText("1 个已在库中，不会重复处理")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "开始处理" }))
    await waitFor(() => {
      expect(createBatch).toHaveBeenCalledWith(["https://youtu.be/bbbbbbbbbbb"], { force_asr: false })
    })

    fireEvent.change(linkBox(), { target: { value: "https://youtu.be/aaaaaaaaaaa" } })
    fireEvent.click(await screen.findByRole("button", { name: "仍然重新处理" }))
    fireEvent.click(screen.getByRole("button", { name: "开始处理" }))
    await waitFor(() => {
      expect(createBatch).toHaveBeenLastCalledWith(["https://youtu.be/aaaaaaaaaaa"], { force_asr: false })
    })
  })
})
