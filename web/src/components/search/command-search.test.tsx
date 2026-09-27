// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { CommandSearch } from "@/components/search/command-search"
import { closeSearch, openSearch } from "@/lib/search-store"

const mocks = vi.hoisted(() => ({
  page: vi.fn(),
  searchTranscripts: vi.fn(),
  navigate: vi.fn(),
  openComposer: vi.fn(),
}))
vi.mock("@/lib/api", () => ({
  api: { archives: { page: mocks.page }, searchTranscripts: mocks.searchTranscripts },
}))
vi.mock("@/lib/router", () => ({ navigate: mocks.navigate, libraryHash: () => "#/files" }))
vi.mock("@/lib/composer-store", () => ({ openComposer: mocks.openComposer }))

const archivePath = "D:\\lib\\Building Anthropic"

beforeEach(() => {
  vi.clearAllMocks()
  Element.prototype.scrollIntoView = vi.fn()
  mocks.page.mockResolvedValue({
    archives: [{ path: archivePath, title: "Building Anthropic", date: "2026-09-24", metadata: {} }],
  })
  mocks.searchTranscripts.mockResolvedValue({
    query: "anthropic",
    groups: [{
      path: archivePath, title: "Building Anthropic", count: 4,
      hits: [{ start_ms: 125_000, speaker: "Dario Amodei", text: "That's why we started Anthropic." }],
    }],
    speakers: [],
    indexing: false,
    progress: null,
  })
})
afterEach(() => {
  act(() => closeSearch())
  cleanup()
})

describe("global search", () => {
  it("opens with Ctrl+K and finds titles and transcript lines", async () => {
    render(<CommandSearch />)
    fireEvent.keyDown(window, { key: "k", ctrlKey: true })
    const input = await screen.findByRole("combobox", { name: "搜索" })

    fireEvent.change(input, { target: { value: "anthropic" } })

    await waitFor(() => expect(screen.getByText("4 处")).toBeTruthy())
    expect(mocks.searchTranscripts).toHaveBeenCalledWith("anthropic")
    expect(screen.getByRole("group", { name: "标题" })).toBeTruthy()
    expect(screen.getByText("2:05")).toBeTruthy()
  })

  it("opens a transcript line at its time with the keyboard", async () => {
    render(<CommandSearch />)
    act(() => openSearch())
    const input = await screen.findByRole("combobox", { name: "搜索" })
    fireEvent.change(input, { target: { value: "anthropic" } })
    await waitFor(() => expect(screen.getByText("4 处")).toBeTruthy())

    // No page matches, so the title comes first and the transcript line second.
    fireEvent.keyDown(input, { key: "ArrowDown" })
    fireEvent.keyDown(input, { key: "Enter" })

    expect(mocks.navigate).toHaveBeenCalledWith(`#/result/archive?path=${encodeURIComponent(archivePath)}&t=125`)
  })

  it("waits for two characters before searching transcripts", async () => {
    render(<CommandSearch />)
    act(() => openSearch())
    const input = await screen.findByRole("combobox", { name: "搜索" })
    fireEvent.change(input, { target: { value: "的" } })

    await waitFor(() => expect(mocks.page).toHaveBeenCalled())
    expect(mocks.searchTranscripts).not.toHaveBeenCalled()
    expect(screen.getByText("再输入一个字，同时搜索字幕内容")).toBeTruthy()
  })

  it("runs page commands", async () => {
    render(<CommandSearch />)
    act(() => openSearch())
    const input = await screen.findByRole("combobox", { name: "搜索" })
    fireEvent.change(input, { target: { value: "新建" } })
    fireEvent.keyDown(input, { key: "Enter" })

    expect(mocks.openComposer).toHaveBeenCalled()
  })
})
