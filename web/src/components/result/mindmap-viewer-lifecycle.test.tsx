/** @vitest-environment jsdom */

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"
import { MindmapViewer } from "./mindmap-viewer"

const pending = vi.hoisted(() => {
  let release: () => void = () => {}
  const ready = new Promise<void>((resolve) => { release = resolve })
  return { ready, release, loading: vi.fn(), create: vi.fn(), destroy: vi.fn(), transform: vi.fn() }
})

vi.mock("markmap-view", async () => {
  pending.loading()
  await pending.ready
  return { Markmap: { create: pending.create } }
})

vi.mock("markmap-lib", () => ({
  Transformer: class {
    transform(markdown: string) {
      pending.transform(markdown)
      return { root: { content: markdown, children: [] } }
    }
  },
}))

vi.mock("./markdown-renderer", () => ({ MarkdownRenderer: () => null }))

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it("ignores a pending render after switching away and renders the current document on return", async () => {
  vi.stubGlobal("matchMedia", () => ({ matches: false }))
  pending.create.mockImplementation(() => ({
    destroy: pending.destroy,
    svg: { interrupt: vi.fn(), selectAll: () => ({ interrupt: vi.fn() }) },
  }))

  const view = render(<MindmapViewer markdown="# First" fillContainer />)
  await waitFor(() => expect(pending.loading).toHaveBeenCalledOnce())
  fireEvent.click(screen.getByRole("button", { name: "切换到 Markdown" }))
  view.rerender(<MindmapViewer markdown="# Second" fillContainer />)
  await waitFor(() => expect(pending.transform).toHaveBeenLastCalledWith("# Second"))

  await act(async () => { pending.release() })
  expect(pending.create).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole("button", { name: "切换到思维导图" }))
  await waitFor(() => expect(pending.create).toHaveBeenCalledOnce())
  expect(pending.create.mock.calls[0][2].content).toBe("# Second")
  expect(pending.destroy).not.toHaveBeenCalled()
  view.unmount()
  expect(pending.destroy).toHaveBeenCalledOnce()
})
