/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import "@testing-library/jest-dom/vitest"
import { afterEach, describe, expect, it, vi } from "vitest"

import { MobileFilterSheet } from "@/components/app-shell/mobile-filter-sheet"

afterEach(cleanup)

function library() {
  return {
    statuses: ["failed" as const],
    media: ["video" as const],
    sources: ["bilibili" as const],
    duplicates: false,
    sort: "title_asc" as const,
    setStatuses: vi.fn(),
    setMedia: vi.fn(),
    setSources: vi.fn(),
    setDuplicates: vi.fn(),
    setSort: vi.fn(),
    resetFilters: vi.fn(),
  }
}

describe("MobileFilterSheet", () => {
  it("adds and removes values within a group and restores the defaults", () => {
    const controls = library()
    render(<MobileFilterSheet open onOpenChange={vi.fn()} library={controls} />)

    expect(screen.getByRole("button", { name: "视频" })).toHaveAttribute("aria-pressed", "true")
    fireEvent.click(screen.getByRole("button", { name: "音频" }))
    expect(controls.setMedia).toHaveBeenCalledWith(["video", "audio"])

    fireEvent.click(screen.getByRole("button", { name: "失败" }))
    expect(controls.setStatuses).toHaveBeenCalledWith([])

    fireEvent.click(screen.getByRole("button", { name: "只看处理过多次的来源" }))
    expect(controls.setDuplicates).toHaveBeenCalledWith(true)

    fireEvent.click(screen.getByRole("button", { name: "恢复默认" }))
    expect(controls.resetFilters).toHaveBeenCalled()
    expect(controls.setSort).toHaveBeenCalledWith("created_desc")
  })
})
