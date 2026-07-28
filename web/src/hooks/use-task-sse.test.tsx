/** @vitest-environment jsdom */

import { act, cleanup, renderHook } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { subscribeTaskEvents } from "@/lib/api"
import { useTaskSSE, type FileReadyEvent } from "./use-task-sse"

vi.mock("@/lib/api", () => ({
  subscribeTaskEvents: vi.fn(),
}))

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe("useTaskSSE", () => {
  it("dispatches queued events to the latest committed handlers", () => {
    const unsubscribe = vi.fn()
    let emit: ((event: {
      task_id: string
      type: string
      data: Record<string, unknown>
      timestamp: string
    }) => void) | undefined
    vi.mocked(subscribeTaskEvents).mockImplementation((_taskId, callback) => {
      emit = callback
      return unsubscribe
    })
    const first = vi.fn()
    const latest = vi.fn()
    const { rerender, unmount } = renderHook(
      ({ onFileReady }: { onFileReady: (event: FileReadyEvent) => void }) => {
        useTaskSSE("task-1", { onFileReady })
      },
      { initialProps: { onFileReady: first } },
    )

    rerender({ onFileReady: latest })
    act(() => {
      emit?.({
        task_id: "task-1",
        type: "file_ready",
        data: { file: "transcript_polished.srt", path: "archive/transcript_polished.srt" },
        timestamp: "2026-07-28T00:00:00Z",
      })
    })

    expect(latest).toHaveBeenCalledWith({
      file: "transcript_polished.srt",
      path: "archive/transcript_polished.srt",
    })
    expect(first).not.toHaveBeenCalled()
    unmount()
    expect(unsubscribe).toHaveBeenCalledOnce()
  })
})
