import { describe, expect, it } from "vitest"

import { checkpointAction, sourceLabel, taskDisplayTitle, taskTimeHint } from "./task-display"

describe("taskDisplayTitle", () => {
  it("uses the archive folder name and drops the duplicate suffix", () => {
    expect(taskDisplayTitle({
      source: "https://www.bilibili.com/video/BV1Y5bX6yEuH/?spm_id_from=333.1387.upload.video_card.click",
      result: { output_dir: "D:\\Video\\MediaProcessPipeline\\《江泽民》 第11集 《人民情怀》 (2)" },
    })).toBe("《江泽民》 第11集 《人民情怀》")
  })

  it("falls back to a recognizable source label instead of the URL tail", () => {
    expect(taskDisplayTitle({
      source: "https://www.bilibili.com/video/BV1Y5bX6yEuH/?spm_id_from=333.1387.upload.video_card.click",
      result: null,
    })).toBe("B站 BV1Y5bX6yEuH")
  })
})

describe("sourceLabel", () => {
  it("names YouTube videos by id", () => {
    expect(sourceLabel("https://www.youtube.com/watch?v=abc123XYZ")).toBe("YouTube abc123XYZ")
    expect(sourceLabel("https://youtu.be/abc123XYZ")).toBe("YouTube abc123XYZ")
  })

  it("uses the file name for local paths", () => {
    expect(sourceLabel("C:\\Users\\me\\Videos\\interview_0921.mp4")).toBe("interview_0921.mp4")
  })

  it("keeps host and last path segment for other links", () => {
    expect(sourceLabel("https://example.com/posts/hello-world")).toBe("example.com · hello-world")
  })
})

describe("taskTimeHint", () => {
  it("shows when a task was paused rather than hours since creation", () => {
    expect(taskTimeHint({ status: "paused", updated_at: "2026-08-17T01:14:05" })).toBe("暂停于 08-17")
    expect(taskTimeHint({ status: "queued", updated_at: "2026-08-17T01:14:05" })).toBe("排队中")
    expect(taskTimeHint({ status: "processing", updated_at: "2026-08-17T01:14:05" })).toBeNull()
  })
})

describe("checkpointAction", () => {
  it("labels checkpoint reruns by what they do for each state", () => {
    expect(checkpointAction("failed")?.label).toBe("从断点继续")
    expect(checkpointAction("cancelled")?.label).toBe("从断点继续")
    expect(checkpointAction("completed")?.label).toBe("重新生成摘要和导图")
    expect(checkpointAction("paused")).toBeNull()
    expect(checkpointAction("processing")).toBeNull()
    expect(checkpointAction(null)).toBeNull()
  })
})
