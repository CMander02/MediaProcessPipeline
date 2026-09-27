// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { RecentSubmissions } from "@/components/composer/recent-submissions"

const mocks = vi.hoisted(() => ({ list: vi.fn(), openTask: vi.fn() }))
vi.mock("@/lib/api", () => ({ api: { tasks: { list: mocks.list } } }))
vi.mock("@/lib/open-task", () => ({ openTask: mocks.openTask }))
vi.mock("@/hooks/use-active-tasks", () => ({ useActiveTasks: () => ({ tasks: [], loaded: true }) }))

afterEach(cleanup)

describe("recent submissions", () => {
  it("lists the latest tasks with their state and opens one", async () => {
    const task = {
      id: "task-1", status: "processing", progress: 0.42, current_step: "transcribe",
      source: "https://youtu.be/abc", result: { output_dir: "D:/lib/Building Anthropic" },
      updated_at: "2026-09-27T22:00:00", created_at: "2026-09-27T21:50:00",
    }
    mocks.list.mockResolvedValue([task])

    render(<RecentSubmissions />)

    fireEvent.click(await screen.findByText("Building Anthropic"))
    expect(screen.getByText(/42%/)).toBeTruthy()
    expect(mocks.list).toHaveBeenCalledWith(undefined, 8)
    expect(mocks.openTask).toHaveBeenCalledWith(task)
  })

  it("says what will appear when nothing was submitted yet", async () => {
    mocks.list.mockResolvedValue([])
    render(<RecentSubmissions />)
    expect(await screen.findByText("提交后会列在这里，可以随时看进度。")).toBeTruthy()
  })
})
