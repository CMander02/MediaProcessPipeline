// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react"
import { afterEach, beforeEach, expect, it, vi } from "vitest"
import { useLibrarySync } from "./use-library-sync"

const m = vi.hoisted(() => ({
  online: true, platform: { isNative: true }, refresh: vi.fn(), sync: vi.fn(),
  listener: null as null | ((event: { type: string }) => void),
}))
vi.mock("@/hooks/use-app-access-context", () => ({ useAppAccess: () => ({ online: m.online }) }))
vi.mock("@/platform/use-platform", () => ({ usePlatform: () => m.platform }))
vi.mock("@/hooks/use-library-directory", () => ({ refreshLibraryDirectory: m.refresh }))
vi.mock("@/repositories/archive-repository", () => ({ createArchiveRepository: () => ({ triggerSync: m.sync }) }))
vi.mock("@/lib/api", () => ({ subscribeAllEvents: (listener: typeof m.listener) => {
  m.listener = listener
  return () => { m.listener = null }
} }))

beforeEach(() => {
  vi.useFakeTimers(); vi.clearAllMocks()
  m.online = true
  m.refresh.mockResolvedValue(undefined)
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible")
})
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks() })
async function tick(ms = 300) { await act(async () => { await vi.advanceTimersByTimeAsync(ms) }) }

it("coalesces remote folder and task events, then refreshes pages and offline files", async () => {
  const changed = vi.fn()
  window.addEventListener("mpp:library-refresh", changed)
  const hook = renderHook(useLibrarySync)
  await tick()
  expect(m.refresh).toHaveBeenCalledTimes(1)
  m.listener?.({ type: "library_changed" })
  m.listener?.({ type: "completed" })
  await tick()
  expect(m.refresh).toHaveBeenCalledTimes(2)
  expect(m.sync).toHaveBeenCalledTimes(2)
  expect(changed).toHaveBeenCalledTimes(2)
  hook.unmount()
  await tick(30_000)
  expect(m.refresh).toHaveBeenCalledTimes(2)
  expect(m.listener).toBeNull()
  window.removeEventListener("mpp:library-refresh", changed)
})

it("recovers missed events periodically and immediately after reconnection or resume", async () => {
  renderHook(useLibrarySync)
  await tick()
  await tick(15_000)
  expect(m.refresh).toHaveBeenCalledTimes(2)
  window.dispatchEvent(new Event("mpp:events-connected"))
  window.dispatchEvent(new Event("mpp:app-resume"))
  await tick()
  expect(m.refresh).toHaveBeenCalledTimes(3)
})

it("pauses in background and offline, and ignores progress events", async () => {
  const hook = renderHook(useLibrarySync)
  await tick()
  m.listener?.({ type: "progress" })
  await tick()
  expect(m.refresh).toHaveBeenCalledTimes(1)
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden")
  await tick(30_000)
  expect(m.refresh).toHaveBeenCalledTimes(1)
  m.online = false
  hook.rerender()
  expect(m.listener).toBeNull()
})

it("does not overlap requests or publish a late response after unmount", async () => {
  let finish!: () => void
  m.refresh.mockReturnValue(new Promise<void>((resolve) => { finish = resolve }))
  const hook = renderHook(useLibrarySync)
  await tick()
  m.listener?.({ type: "library_changed" })
  await tick()
  expect(m.refresh).toHaveBeenCalledTimes(1)
  hook.unmount()
  await act(async () => finish())
  expect(m.sync).not.toHaveBeenCalled()
})
