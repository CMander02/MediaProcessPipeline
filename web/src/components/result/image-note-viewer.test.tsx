/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import "@testing-library/jest-dom/vitest"
import { afterEach, describe, expect, it, vi } from "vitest"

import { ImageNoteViewer, type ImageDescription } from "./image-note-viewer"


const descriptions: ImageDescription[] = [
  { index: 10, image_path: "D:/archive/10.jpg", kind: "content", text: "第一张" },
  { index: 20, image_path: "D:/archive/20.jpg", kind: "content", text: "第二张" },
]

afterEach(cleanup)

describe("ImageNoteViewer", () => {
  it("follows an externally selected description index", () => {
    const { rerender } = render(
      <ImageNoteViewer descriptions={descriptions} activeIndex={20} />,
    )

    expect(screen.getByAltText("图片 2")).toHaveAttribute("src", expect.stringContaining("20.jpg"))

    rerender(<ImageNoteViewer descriptions={descriptions} activeIndex={10} />)
    expect(screen.getByAltText("图片 1")).toHaveAttribute("src", expect.stringContaining("10.jpg"))
  })

  it("reports navigation using the durable description index", () => {
    const onImageIndexChange = vi.fn()
    render(
      <ImageNoteViewer descriptions={descriptions} onImageIndexChange={onImageIndexChange} />,
    )

    fireEvent.click(screen.getAllByRole("button")[2])
    expect(onImageIndexChange).toHaveBeenCalledWith(20)
    expect(screen.getByAltText("图片 2")).toBeInTheDocument()
  })
})
