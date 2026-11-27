/** @vitest-environment jsdom */
import { cleanup, render, screen } from "@testing-library/react"
import "@testing-library/jest-dom/vitest"
import { afterEach, describe, expect, it } from "vitest"
import { ArchiveCoverFallback } from "./archive-cover-fallback"
import { PlatformIcon } from "./platform-icon"
import { resolveArchivePlatform } from "@/lib/archive-filters"

afterEach(() => {
  cleanup()
})

describe("resolveArchivePlatform and Xiaoyuzhou / fallback cover", () => {
  it("resolves xiaoyuzhou from extra metadata and source url", () => {
    expect(resolveArchivePlatform({ platform: null, extra: { platform: "xiaoyuzhou" } })).toBe("xiaoyuzhou")
    expect(resolveArchivePlatform({ source_url: "https://www.xiaoyuzhoufm.com/episode/abc" })).toBe("xiaoyuzhou")
    expect(resolveArchivePlatform({ platform: "zhihu" })).toBe("zhihu")
  })

  it("renders brand SVG icon for xiaoyuzhou", () => {
    render(<PlatformIcon platform="xiaoyuzhou" />)
    const img = screen.getByRole("img", { name: "小宇宙" })
    expect(img).toBeInTheDocument()
  })

  it("renders dynamic fallback cover with source icon, source label, media type badge and hint without repeating title", () => {
    render(
      <ArchiveCoverFallback
        title="没有封面的知乎回答"
        platform="zhihu"
        uploader="答主"
        hintText="这是一段摘要提示"
        mediaLabel="正文"
        isTextNote
        variant="card"
      />,
    )
    expect(screen.getByText("知乎")).toBeInTheDocument()
    expect(screen.getByText("正文")).toBeInTheDocument()
    expect(screen.getByRole("img", { name: "知乎" })).toBeInTheDocument()
    expect(screen.getByText("这是一段摘要提示")).toBeInTheDocument()
    expect(screen.queryByText("没有封面的知乎回答")).not.toBeInTheDocument()
  })
})
