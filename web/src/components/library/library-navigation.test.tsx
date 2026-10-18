/** @vitest-environment jsdom */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { LibraryScopeSwitch, LibraryViewControls } from "./library-navigation"

const { library, updatePreferences } = vi.hoisted(() => ({
  library: { view: "split", scope: "all", folder: undefined as string | undefined, setView: vi.fn(), setScope: vi.fn(), setLocation: vi.fn() },
  updatePreferences: vi.fn(),
}))

vi.mock("@/hooks/use-library-controls", () => ({
  useLibraryControls: () => library,
}))
vi.mock("@/hooks/use-app-access-context", () => ({
  useAppAccess: () => ({ online: true, capabilities: { archive_mutation: false } }),
}))
vi.mock("@/hooks/use-library-directory", () => ({ libraryChanged: vi.fn(), useLibraryDirectory: vi.fn() }))
vi.mock("@/hooks/use-preferences", () => ({ usePreferences: () => ({ update: updatePreferences }) }))

beforeEach(() => { library.scope = "all"; library.folder = undefined })

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  vi.unstubAllGlobals()
})

describe("library view controls", () => {
  it("labels the same view as split on desktop and list on narrow screens", () => {
    const query = Object.assign(new EventTarget(), { matches: false })
    vi.stubGlobal("matchMedia", vi.fn(() => query))
    render(<LibraryViewControls />)

    const splitButton = screen.getByRole("button", { name: "分栏视图" })
    expect(splitButton.getAttribute("aria-pressed")).toBe("true")
    expect(window.matchMedia).toHaveBeenCalledWith("(max-width: 1099px)")
    fireEvent.click(splitButton)
    expect(library.setView).toHaveBeenLastCalledWith("split")

    act(() => {
      query.matches = true
      query.dispatchEvent(new Event("change"))
    })
    expect(screen.queryByRole("button", { name: "分栏视图" })).toBeNull()
    const listButton = screen.getByRole("button", { name: "列表视图" })
    expect(listButton.getAttribute("title")).toBe("列表视图")
    fireEvent.click(listButton)
    expect(library.setView).toHaveBeenLastCalledWith("split")

    fireEvent.click(screen.getByRole("button", { name: "网格视图" }))
    expect(library.setView).toHaveBeenLastCalledWith("grid")
    expect(screen.queryByRole("button", { name: "显示文件列表" })).toBeNull()
  })

  it("lets the current all scope return to the list from a result", () => {
    render(<LibraryScopeSwitch />)
    fireEvent.click(screen.getByRole("radio", { name: "全部" }))
    expect(library.setScope).toHaveBeenCalledWith("all")
  })

  it("keeps the folder scope shortcut to the folder overview", () => {
    library.scope = "folders"
    library.folder = "course"
    render(<LibraryScopeSwitch />)
    fireEvent.click(screen.getByRole("radio", { name: "文件夹" }))
    expect(library.setLocation).toHaveBeenCalledWith({ folder: undefined, item: undefined })
  })
})
