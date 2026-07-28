/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import "@testing-library/jest-dom/vitest"
import { afterEach, describe, expect, it, vi } from "vitest"

import { SubmitPage } from "./submit-page"
import { api, type Settings, type WorkerStatus } from "@/lib/api"
import { PREFERRED_WORKER_OPTION } from "@/lib/task-routing"
import { navigate } from "@/lib/router"

vi.mock("@/lib/router", () => ({ navigate: vi.fn() }))

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.mocked(navigate).mockReset()
})

function mockBootstrap(
  settings: Partial<Settings> = {},
  workers: WorkerStatus[] = [],
) {
  vi.spyOn(api.settings, "get").mockResolvedValue({
    default_task_executor: "server",
    remote_sync_enabled: false,
    ...settings,
  } as Settings)
  vi.spyOn(api.workers, "list").mockResolvedValue({ workers })
}

describe("SubmitPage Bilibili collection selection", () => {
  it("opens the collection list and submits the selected entries as one batch", async () => {
    mockBootstrap()
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
    const createBatch = vi.spyOn(api.tasks, "createBatch").mockResolvedValue([])

    render(<SubmitPage />)

    fireEvent.change(screen.getByPlaceholderText("粘贴视频链接或本地路径..."), {
      target: { value: "https://www.bilibili.com/video/BV1DK4y1b7bY/" },
    })
    fireEvent.click(screen.getByRole("button", { name: "开始处理" }))

    expect(await screen.findByText("零基础平面设计入门系列")).toBeInTheDocument()
    expect(screen.getByText("第一集 文字排版")).toBeInTheDocument()
    expect(screen.getByText("第二集 色彩理论")).toBeInTheDocument()
    expect(inspect).toHaveBeenCalledWith("https://www.bilibili.com/video/BV1DK4y1b7bY/")
    expect(createBatch).not.toHaveBeenCalled()

    fireEvent.click(screen.getByLabelText("选择 第二集 色彩理论"))
    fireEvent.click(screen.getByRole("button", { name: "开始处理" }))

    await waitFor(() => {
      expect(createBatch).toHaveBeenCalledWith(
        ["https://www.bilibili.com/video/BV1DK4y1b7bY"],
        {},
        {
          origin_client: "web",
          requested_executor: "server",
        },
      )
    })
    expect(navigate).toHaveBeenCalledWith("#/files")
  })
})

describe("SubmitPage processing target guard", () => {
  it("restores the default server target and blocks a local path before submission", async () => {
    mockBootstrap({
      default_task_executor: "server",
      remote_sync_enabled: true,
    })
    const createBatch = vi.spyOn(api.tasks, "createBatch").mockResolvedValue([])

    render(<SubmitPage />)

    const target = screen.getByLabelText("处理端")
    await waitFor(() => expect(target).toHaveValue("server"))
    fireEvent.change(screen.getByPlaceholderText("粘贴视频链接或本地路径..."), {
      target: { value: "C:\\Media\\clip.mp4" },
    })

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "服务器无法访问此 EXE 上的本地文件或文件夹",
    )
    expect(screen.getByRole("button", { name: "开始处理" })).toBeDisabled()
    fireEvent.submit(
      screen.getByPlaceholderText("粘贴视频链接或本地路径...").closest("form")!,
    )
    expect(await screen.findAllByText(/请选择 EXE 或具体 Worker 处理/)).toHaveLength(2)
    expect(createBatch).not.toHaveBeenCalled()
  })

  it("restores the EXE default and keeps URL tasks available", async () => {
    mockBootstrap({
      default_task_executor: "exe",
      remote_sync_enabled: true,
    })
    const createBatch = vi.spyOn(api.tasks, "createBatch").mockResolvedValue([])

    render(<SubmitPage />)

    const target = screen.getByLabelText("处理端")
    await waitFor(() => expect(target).toHaveValue("exe"))
    fireEvent.change(screen.getByPlaceholderText("粘贴视频链接或本地路径..."), {
      target: { value: "https://example.com/video" },
    })
    fireEvent.click(screen.getByRole("button", { name: "开始处理" }))

    await waitFor(() => {
      expect(createBatch).toHaveBeenCalledWith(
        ["https://example.com/video"],
        {},
        {
          origin_client: "web",
          requested_executor: "exe",
        },
      )
    })
  })

  it("allows a local path for a concrete worker and sends both routing fields", async () => {
    mockBootstrap(
      {
        default_task_executor: "server",
        remote_sync_enabled: true,
      },
      [{
        id: "desktop-a",
        name: "剪辑工作站",
        executor: "exe",
        online: true,
        status: "online",
      }],
    )
    const createBatch = vi.spyOn(api.tasks, "createBatch").mockResolvedValue([])

    render(<SubmitPage />)

    const target = screen.getByLabelText("处理端")
    await screen.findByRole("option", { name: "剪辑工作站（在线）" })
    fireEvent.change(target, { target: { value: "worker:desktop-a" } })
    fireEvent.change(screen.getByPlaceholderText("粘贴视频链接或本地路径..."), {
      target: { value: "D:\\Media\\clip.mp4" },
    })
    expect(screen.getByRole("button", { name: "开始处理" })).toBeEnabled()
    fireEvent.click(screen.getByRole("button", { name: "开始处理" }))

    await waitFor(() => {
      expect(createBatch).toHaveBeenCalledWith(
        ["D:\\Media\\clip.mp4"],
        { [PREFERRED_WORKER_OPTION]: "desktop-a" },
        {
          origin_client: "web",
          requested_executor: "exe",
        },
      )
    })
  })

  it("blocks a staged browser upload for server and allows it after switching to EXE", async () => {
    mockBootstrap({
      default_task_executor: "server",
      remote_sync_enabled: true,
    })
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:mpp-test")
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {})
    const originalCreateElement = document.createElement.bind(document)
    vi.spyOn(document, "createElement").mockImplementation(((tagName: string, options?: ElementCreationOptions) => {
      const element = originalCreateElement(tagName, options)
      if (tagName === "video" || tagName === "audio") {
        queueMicrotask(() => {
          element.dispatchEvent(new Event("error"))
        })
      }
      return element
    }) as typeof document.createElement)
    vi.spyOn(api.pipeline, "stage").mockResolvedValue({
      staging_id: "stage-1",
      path: "C:\\MPP\\staging\\clip.mp4",
      filename: "clip.mp4",
      title: "clip",
      size: 4,
      media_type: "video",
    })
    const createBatch = vi.spyOn(api.tasks, "createBatch").mockResolvedValue([])

    render(<SubmitPage />)

    fireEvent.change(screen.getByLabelText("选择本地媒体文件"), {
      target: {
        files: [new File(["clip"], "clip.mp4", { type: "video/mp4" })],
      },
    })
    expect(await screen.findByText("clip.mp4")).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "开始处理" })).toBeDisabled()
    })

    fireEvent.change(screen.getByLabelText("处理端"), {
      target: { value: "exe" },
    })
    expect(screen.getByRole("button", { name: "开始处理" })).toBeEnabled()
    fireEvent.click(screen.getByRole("button", { name: "开始处理" }))

    await waitFor(() => {
      expect(createBatch).toHaveBeenCalledWith(
        ["C:\\MPP\\staging\\clip.mp4"],
        {},
        {
          origin_client: "web",
          requested_executor: "exe",
        },
      )
    })
  })
})
