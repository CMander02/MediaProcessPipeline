/** @vitest-environment jsdom */
import { act, cleanup, renderHook, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { libraryHash, navigate, parseHash, previousHash } from "@/lib/router"
import { useLibraryControls } from "./use-library-controls"

function controls() {
  return renderHook(() => useLibraryControls()).result
}

afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe("library navigation history", () => {
  it("switching to the grid inside a folder puts the library root under it", () => {
    act(() => navigate("#/files?view=split&item=A"))
    const library = controls()
    act(() => library.current.setLocation({ view: "split", folder: "f1", item: "B" }))
    expect(previousHash()).toBe("#/files?view=split&item=A")

    act(() => library.current.setView("grid"))
    // Back leaves the folder for the folder overview, not for the split view.
    expect(window.location.hash).toBe("#/files?folder=f1")
    expect(previousHash()).toBe("#/files")
  })

  it("view switches replace the entry instead of adding a Back step", () => {
    act(() => navigate("#/files?page=2"))
    const library = controls()
    act(() => library.current.setLocation({ folder: "f2", item: undefined }))
    act(() => library.current.setView("split"))
    expect(window.location.hash).toBe("#/files?view=split&folder=f2")
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

  it("closing the split view returns to the folder grid it was opened from", () => {
    act(() => navigate("#/files?folder=f4"))
    const library = controls()
    act(() => library.current.setLocation({ view: "split", folder: "f4", item: "C" }))
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

  it("switching to the grid clears the open item and its selected result", () => {
    act(() => navigate("#/files?q=unique-grid-switch&page=3&view=split&scope=all&item=C&attempt=attempt-1&viewerTask=task-1&generation=generation-1"))
    const library = controls()
    act(() => library.current.setView("grid"))
    expect(window.location.hash).toBe("#/files?q=unique-grid-switch&page=3&scope=all")

    act(() => library.current.setView("split"))
    expect(window.location.hash).toBe("#/files?q=unique-grid-switch&page=3&view=split&scope=all")
    expect(library.current.item).toBeUndefined()
    expect(library.current.attempt).toBeUndefined()
    expect(library.current.viewerTask).toBeUndefined()
    expect(library.current.generation).toBeUndefined()
  })

  it("selecting the grid clears detail parameters from an old grid link", () => {
    act(() => navigate("#/files?q=legacy-grid&item=D&attempt=attempt-2&viewerTask=task-2&generation=generation-2"))
    const library = controls()
    act(() => library.current.setView("grid"))
    expect(window.location.hash).toBe("#/files?q=legacy-grid")
  })
})

describe("library controls on result pages", () => {
  it("restores the source folder, filters and page, and the selected grid button returns there", async () => {
    const source = "#/files?q=source-grid&media=video&src=bilibili&status=completed&sort=title_asc&page=4&folder=course"
    act(() => navigate(source))
    const library = controls()
    act(() => navigate("#/result/archive?path=D%3A%5Carchive&taskId=task-1&q=result-query&page=99&folder=wrong"))

    expect(library.current).toMatchObject({
      search: "source-grid", media: ["video"], sources: ["bilibili"], statuses: ["completed"],
      sort: "title_asc", page: 4, folder: "course", view: "grid",
    })
    expect(libraryHash()).toBe(source)

    act(() => library.current.setView("grid"))
    await waitFor(() => expect(window.location.hash).toBe(source))
  })

  it("opens split view with the source state instead of result URL parameters", () => {
    act(() => navigate("#/files?q=source-split&media=audio&src=youtube&page=3&folder=lectures"))
    const library = controls()
    act(() => navigate("#/result/archive?path=D%3A%5Carchive&taskId=task-2&q=wrong&view=browse&page=99"))

    act(() => library.current.setView("split"))
    expect(parseHash(window.location.hash)).toEqual({
      page: "files",
      params: { q: "source-split", media: "audio", src: "youtube", page: "3", view: "split", folder: "lectures" },
    })
  })

  it("searches from a result using the source folder and filters, starting at page 1", () => {
    act(() => navigate("#/files?q=old-search&media=video&src=bilibili&page=5&folder=course-search"))
    const library = controls()
    act(() => navigate("#/result/task/task-3?path=D%3A%5Carchive&taskId=task-3&src=youtube&folder=wrong"))

    act(() => library.current.setSearch("new-search"))
    expect(parseHash(window.location.hash)).toEqual({
      page: "files", params: { q: "new-search", media: "video", src: "bilibili", folder: "course-search" },
    })
  })

  it("the current scope button returns from a result to the same list page", async () => {
    const source = "#/files?q=scope-return&page=6&scope=all"
    act(() => navigate(source))
    const library = controls()
    act(() => navigate("#/result/archive?path=D%3A%5Carchive&taskId=task-4"))

    act(() => library.current.setScope("all"))
    await waitFor(() => expect(window.location.hash).toBe(source))
  })
})
