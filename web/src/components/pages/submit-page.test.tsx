/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import "@testing-library/jest-dom/vitest"
import { afterEach, describe, expect, it, vi } from "vitest"

import { SubmitPage } from "./submit-page"
import { api } from "@/lib/api"
import { navigate } from "@/lib/router"

vi.mock("@/lib/router", () => ({ navigate: vi.fn() }))

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.mocked(navigate).mockReset()
})

describe("SubmitPage Bilibili collection selection", () => {
  it("opens the collection list and submits the selected entries as one batch", async () => {
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
      )
    })
    expect(navigate).toHaveBeenCalledWith("#/files")
  })
})
