/** @vitest-environment jsdom */

import { act, cleanup, renderHook } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { useMediaSync } from "./use-media-sync"

afterEach(cleanup)

function videoWithDuration(duration: number): HTMLVideoElement {
  const video = document.createElement("video")
  Object.defineProperty(video, "duration", {
    configurable: true,
    value: duration,
  })
  return video
}

describe("useMediaSync", () => {
  it("uses the latest callback after a committed rerender", () => {
    const first = vi.fn()
    const latest = vi.fn()
    const { result, rerender } = renderHook(
      ({ onTimeUpdate }: { onTimeUpdate: (time: number) => void }) =>
        useMediaSync({ subtitles: [], onTimeUpdate }),
      { initialProps: { onTimeUpdate: first } },
    )
    const video = videoWithDuration(120)
    let unbind: (() => void) | undefined
    act(() => {
      unbind = result.current.bindMedia(video)
    })

    rerender({ onTimeUpdate: latest })
    act(() => {
      video.currentTime = 42
      video.dispatchEvent(new Event("timeupdate"))
    })

    expect(latest).toHaveBeenCalledWith(42)
    expect(first).not.toHaveBeenCalled()
    act(() => unbind?.())
  })

  it("applies the new initial time when a media binding changes", () => {
    const { result, rerender } = renderHook(
      ({ initialTime }: { initialTime: number }) =>
        useMediaSync({ subtitles: [], initialTime }),
      { initialProps: { initialTime: 12 } },
    )
    const video = videoWithDuration(120)
    let unbind: (() => void) | undefined
    act(() => {
      unbind = result.current.bindMedia(video)
    })
    expect(video.currentTime).toBe(12)

    rerender({ initialTime: 34 })
    act(() => unbind?.())
    act(() => {
      unbind = result.current.bindMedia(video)
    })

    expect(video.currentTime).toBe(34)
    act(() => unbind?.())
  })
})
