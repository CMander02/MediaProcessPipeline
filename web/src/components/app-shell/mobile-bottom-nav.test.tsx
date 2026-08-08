/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import "@testing-library/jest-dom/vitest"
import { afterEach, describe, expect, it, vi } from "vitest"

import { MobileBottomNav } from "@/components/app-shell/mobile-bottom-nav"
import { navigate } from "@/lib/router"

vi.mock("@/lib/router", () => ({ navigate: vi.fn() }))

afterEach(() => {
  cleanup()
  vi.mocked(navigate).mockReset()
})

describe("MobileBottomNav", () => {
  it("keeps the shared navigation order and exposes the active page", () => {
    render(<MobileBottomNav activePage="submit" />)

    const links = screen.getAllByRole("button")
    expect(links.map((link) => link.textContent)).toEqual(["文件", "处理", "后端"])
    expect(screen.getByRole("button", { name: "处理" })).toHaveAttribute("aria-current", "page")

    fireEvent.click(screen.getByRole("button", { name: "后端" }))
    expect(navigate).toHaveBeenCalledWith("#/backend")
  })
})
