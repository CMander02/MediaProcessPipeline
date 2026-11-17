// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { useResultPanelLayout } from "./use-result-layout"

const stored = new Map<string, string>()
beforeEach(() => {
  stored.clear()
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => stored.get(key) ?? null,
    setItem: (key: string, value: string) => stored.set(key, value),
  })
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe("result panel layout", () => {
  it("starts with equal panels and restores the saved ratio across archive mounts", () => {
    const first = renderHook(() => useResultPanelLayout())
    expect(first.result.current.defaultLayout).toEqual({ media: 50, content: 50 })
    act(() => first.result.current.onLayoutChanged({ media: 32, content: 68 }))
    expect(first.result.current.defaultLayout).toEqual({ media: 32, content: 68 })
    first.unmount()
    const next = renderHook(() => useResultPanelLayout())
    expect(next.result.current.defaultLayout).toEqual({ media: 32, content: 68 })
    expect(stored.get("mpp-result-panel-ratio")).toBe("32")
  })

  it.each(["invalid", "NaN", "Infinity", "", "19", "71"])("uses equal panels when the saved ratio is %s", (ratio) => {
    stored.set("mpp-result-panel-ratio", ratio)
    const hook = renderHook(() => useResultPanelLayout())
    expect(hook.result.current.defaultLayout).toEqual({ media: 50, content: 50 })
  })

  it("keeps resizing available when browser storage is unavailable", () => {
    vi.stubGlobal("localStorage", {
      getItem: () => { throw new Error("Unavailable") },
      setItem: () => { throw new Error("Unavailable") },
    })
    const hook = renderHook(() => useResultPanelLayout())
    act(() => hook.result.current.onLayoutChanged({ media: 40, content: 60 }))
    expect(hook.result.current.defaultLayout).toEqual({ media: 40, content: 60 })
  })
})
