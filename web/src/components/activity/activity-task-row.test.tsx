// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"

import { ActivityTaskRow } from "@/components/activity/activity-task-row"
import type { Task } from "@/lib/api"

afterEach(cleanup)

const task = (overrides: Partial<Task>): Task => ({
  id: "task-1",
  status: "paused",
  progress: 0.17,
  current_step: "transcribe",
  source: "https://youtu.be/abc",
  result: { output_dir: "D:/lib/江泽民 第11集" },
  created_at: "2026-08-16T23:22:00",
  updated_at: "2026-08-17T01:14:05",
  ...overrides,
} as Task)

describe("task rows", () => {
  it("shows where a paused task stopped, not when", () => {
    render(<ActivityTaskRow task={task({})} onOpen={() => {}} />)
    expect(screen.getByText("转录音频 · 17%")).toBeTruthy()
    expect(screen.getByText("已暂停：")).toBeTruthy()
    expect(screen.queryByText(/暂停于/)).toBeNull()
  })

  it("keeps the step and progress for running tasks", () => {
    render(<ActivityTaskRow task={task({ status: "processing", progress: 0.5, current_step: "speaker_review" })} onOpen={() => {}} />)
    expect(screen.getByText("说话人修正 · 50%")).toBeTruthy()
  })
})
