/** @vitest-environment jsdom */
import { describe, expect, it } from "vitest"
import type { LibraryMatch } from "@/lib/api"
import { libraryStateOf } from "./source-meta"

const match = (status: string | null, processing = false): LibraryMatch => ({
  path: `archives/${status ?? "legacy"}`, title: "同一个视频", status, processing,
})

describe("libraryStateOf", () => {
  it("reuses completed output even when another attempt is active or failed", () => {
    const completed = match("completed")
    const active = match("processing", true)
    const failed = match("failed")
    expect(libraryStateOf([active, failed, completed])).toEqual({ kind: "completed", match: completed })
    expect(libraryStateOf([failed, active])).toEqual({ kind: "processing", match: active })
  })

  it("recognizes completed legacy archives without treating active unknown statuses as complete", () => {
    const legacy = match(null)
    const activeWithoutStatus = match(null, true)
    const activeUnknownStatus = match("resolving", true)
    expect(libraryStateOf([activeWithoutStatus, legacy])).toEqual({ kind: "completed", match: legacy })
    expect(libraryStateOf([activeWithoutStatus])).toEqual({ kind: "processing", match: activeWithoutStatus })
    expect(libraryStateOf([activeUnknownStatus])).toEqual({ kind: "processing", match: activeUnknownStatus })
    expect(libraryStateOf([match("unknown")])?.kind).not.toBe("completed")
  })
})
