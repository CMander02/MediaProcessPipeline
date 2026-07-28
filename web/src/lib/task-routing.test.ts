import { describe, expect, it } from "vitest"

import {
  PREFERRED_WORKER_OPTION,
  isExeLocalSourceBlocked,
  isLocalPathSource,
  preferredWorkerId,
  requestedExecutorForTarget,
  withProcessingTargetOptions,
} from "./task-routing"

describe("task routing", () => {
  it.each([
    "C:\\Media\\clip.mp4",
    "D:/Media/clip.mp3",
    "\\\\nas\\share\\clip.mkv",
    "/srv/media/clip.wav",
    "./media/clip.flac",
    "../media/clip.m4a",
    "clip.mp4",
    "file:///C:/Media/clip.mp4",
  ])("recognizes EXE-local path %s", (source) => {
    expect(isLocalPathSource(source)).toBe(true)
  })

  it.each([
    "https://example.com/video",
    "http://localhost:18000/media",
    "BV1DK4y1b7bY",
    "",
  ])("keeps network source %s available to every target", (source) => {
    expect(isLocalPathSource(source)).toBe(false)
  })

  it("blocks an EXE-local source only when the remote desktop targets the server", () => {
    expect(isExeLocalSourceBlocked({
      remoteSyncEnabled: true,
      target: "server",
      source: "C:\\Media\\clip.mp4",
    })).toBe(true)
    expect(isExeLocalSourceBlocked({
      remoteSyncEnabled: true,
      target: "exe",
      source: "C:\\Media\\clip.mp4",
    })).toBe(false)
    expect(isExeLocalSourceBlocked({
      remoteSyncEnabled: true,
      target: "worker:desktop-a",
      hasStagedFiles: true,
    })).toBe(false)
    expect(isExeLocalSourceBlocked({
      remoteSyncEnabled: false,
      target: "server",
      source: "C:\\Media\\clip.mp4",
    })).toBe(false)
  })

  it("maps a concrete worker to the EXE executor and preferred-worker option", () => {
    const options = withProcessingTargetOptions(
      { force_asr: true },
      "worker:desktop-a",
    )

    expect(requestedExecutorForTarget("worker:desktop-a")).toBe("exe")
    expect(preferredWorkerId("worker:desktop-a")).toBe("desktop-a")
    expect(options).toEqual({
      force_asr: true,
      [PREFERRED_WORKER_OPTION]: "desktop-a",
    })
    expect(withProcessingTargetOptions(options, "server")).toEqual({
      force_asr: true,
    })
  })
})
