/** @vitest-environment jsdom */
import { act, renderHook } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { navigate, previousHash } from "@/lib/router"
import { useLibraryControls } from "./use-library-controls"

function controls() {
  return renderHook(() => useLibraryControls()).result
}

afterEach(() => vi.restoreAllMocks())

describe("library navigation history", () => {
  it("switching to the grid inside a folder puts the library root under it", () => {
    act(() => navigate("#/files?view=browse&item=A"))
    const library = controls()
    act(() => library.current.setLocation({ view: "browse", folder: "f1", item: "B" }))
    expect(previousHash()).toBe("#/files?view=browse&item=A")

    act(() => library.current.setView("grid"))
    // Back leaves the folder for the folder overview, not for the browse view.
    expect(window.location.hash).toBe("#/files?folder=f1&item=B")
    expect(previousHash()).toBe("#/files")
  })

  it("view switches replace the entry instead of adding a Back step", () => {
    act(() => navigate("#/files?page=2"))
    const library = controls()
    act(() => library.current.setLocation({ folder: "f2", item: undefined }))
    act(() => library.current.setView("browse"))
    expect(window.location.hash).toBe("#/files?view=browse&folder=f2")
    expect(previousHash()).toBe("#/files?page=2")
  })

  it("returning to where Back leads goes back instead of growing the history", () => {
    act(() => navigate("#/files?page=3"))
    const library = controls()
    act(() => library.current.setLocation({ folder: "f3", item: undefined }))
    const go = vi.spyOn(window.history, "go").mockImplementation(() => {})
    act(() => library.current.setLocation({ folder: undefined, item: undefined }))
    expect(go).toHaveBeenCalledWith(-1)
  })

  it("closing the browse view returns to the folder grid it was opened from", () => {
    act(() => navigate("#/files?folder=f4"))
    const library = controls()
    act(() => library.current.setLocation({ view: "browse", folder: "f4", item: "C" }))
    const go = vi.spyOn(window.history, "go").mockImplementation(() => {})
    act(() => library.current.setView("grid"))
    expect(go).toHaveBeenCalledWith(-1)
  })

  it("the all scope drops the folder and does not add a Back step", () => {
    act(() => navigate("#/files?folder=f5"))
    const library = controls()
    const before = previousHash()
    act(() => library.current.setScope("all"))
    expect(window.location.hash).toBe("#/files?scope=all")
    expect(previousHash()).toBe(before)
    expect(library.current.scope).toBe("all")
  })
})
