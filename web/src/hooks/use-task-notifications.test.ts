// @vitest-environment jsdom
import { cleanup, renderHook, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { useTaskNotifications } from "@/hooks/use-task-notifications"

type Listener = (event: { task_id: string; type: string; data: Record<string, unknown>; timestamp: string }) => void

const mocks = vi.hoisted(() => ({
  listener: null as Listener | null,
  getTask: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
  navigate: vi.fn(),
  prefs: { taskNotifications: true },
}))
vi.mock("@/hooks/use-app-access-context", () => ({ useAppAccess: () => ({ online: true }) }))
vi.mock("@/hooks/use-preferences", () => ({ getPreferences: () => mocks.prefs }))
vi.mock("@/lib/router", () => ({ navigate: mocks.navigate }))
vi.mock("sonner", () => ({ toast: { success: mocks.success, error: mocks.error } }))
vi.mock("@/lib/api", () => ({
  api: { tasks: { get: mocks.getTask } },
  subscribeAllEvents: (listener: Listener) => {
    mocks.listener = listener
    return () => { mocks.listener = null }
  },
}))

const event = (type: string, data: Record<string, unknown> = {}) =>
  ({ task_id: "task-1", type, data, timestamp: "2026-09-27T00:00:00Z" })

beforeEach(() => {
  vi.clearAllMocks()
  mocks.prefs.taskNotifications = true
  mocks.getTask.mockResolvedValue({ source: "https://youtu.be/abc", result: { output_dir: "D:/lib/Building Anthropic (3)" } })
  window.location.hash = "#/files"
})
afterEach(cleanup)

describe("task notifications", () => {
  it("toasts a finished task once, named after its archive, with a way to open it", async () => {
    renderHook(() => useTaskNotifications())
    mocks.listener?.(event("processing"))
    mocks.listener?.(event("completed"))
    mocks.listener?.(event("completed"))

    await waitFor(() => expect(mocks.success).toHaveBeenCalledTimes(1))
    const [title, options] = mocks.success.mock.calls[0]
    expect(title).toBe("处理完成")
    expect(options.description).toBe("Building Anthropic")
    options.action.onClick()
    expect(mocks.navigate).toHaveBeenCalledWith("#/result/task/task-1")
  })

  it("puts the first line of a failure in the toast", async () => {
    renderHook(() => useTaskNotifications())
    mocks.listener?.(event("failed", { error: "下载失败：HTTP 403\nTraceback ..." }))

    await waitFor(() => expect(mocks.error).toHaveBeenCalledTimes(1))
    expect(mocks.error.mock.calls[0][1].description).toBe("Building Anthropic · 下载失败：HTTP 403")
  })

  it("stays quiet when turned off or when the task's own page is open", async () => {
    renderHook(() => useTaskNotifications())
    mocks.prefs.taskNotifications = false
    mocks.listener?.(event("completed"))
    mocks.prefs.taskNotifications = true
    window.location.hash = "#/result/task/task-1"
    mocks.listener?.(event("failed", { error: "x" }))

    await waitFor(() => expect(mocks.getTask).toHaveBeenCalledTimes(1))
    expect(mocks.success).not.toHaveBeenCalled()
    expect(mocks.error).not.toHaveBeenCalled()
  })
})
