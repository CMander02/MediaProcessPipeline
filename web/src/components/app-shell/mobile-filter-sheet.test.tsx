/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import "@testing-library/jest-dom/vitest"
import { afterEach, describe, expect, it, vi } from "vitest"

import { MobileFilterSheet } from "@/components/app-shell/mobile-filter-sheet"

afterEach(cleanup)

describe("MobileFilterSheet", () => {
  it("applies filter choices and restores the shared defaults", () => {
    const onMediaFilterChange = vi.fn()
    const onSourceFilterChange = vi.fn()
    const onSortChange = vi.fn()

    render(
      <MobileFilterSheet
        open
        onOpenChange={vi.fn()}
        mediaFilter="video"
        sourceFilter="bilibili"
        sort="title_asc"
        onMediaFilterChange={onMediaFilterChange}
        onSourceFilterChange={onSourceFilterChange}
        onSortChange={onSortChange}
      />,
    )

    expect(screen.getByRole("button", { name: "视频" })).toHaveAttribute("aria-pressed", "true")
    fireEvent.click(screen.getByRole("button", { name: "音频" }))
    expect(onMediaFilterChange).toHaveBeenCalledWith("audio")

    fireEvent.click(screen.getByRole("button", { name: "恢复默认" }))
    expect(onMediaFilterChange).toHaveBeenCalledWith("all")
    expect(onSourceFilterChange).toHaveBeenCalledWith("all")
    expect(onSortChange).toHaveBeenCalledWith("created_desc")
  })
})
