// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import type { ArchiveItem } from "@/hooks/use-archives"
import { FilesPage } from "@/components/pages/files-page"

const mocks = vi.hoisted(() => ({
  refresh: vi.fn(),
  getTask: vi.fn(),
  checkpointRerun: vi.fn(),
  navigate: vi.fn(),
}))

const archive: ArchiveItem = {
  title: "断点任务",
  date: "2026-09-02",
  created_at: "2026-09-02T00:00:00",
  path: "D:/Video/MediaProcessPipeline/checkpoint",
  has_transcript: true,
  has_summary: true,
  has_mindmap: true,
  has_video: true,
  has_audio: false,
  has_image: false,
  media_file: null,
  task_id: "task-1",
  metadata: {},
  duration_seconds: 60,
  analysis: {},
}

vi.mock("@/hooks/use-archive-page", () => ({
  useArchivePage: () => ({
    archives: [archive],
    total: 1,
    page: 1,
    loading: false,
    refresh: mocks.refresh,
    removeArchive: vi.fn(),
  }),
}))

vi.mock("@/hooks/use-preferences", () => ({
  usePreferences: () => ({ update: vi.fn() }),
}))

vi.mock("@/hooks/use-app-access-context", () => ({
  useAppAccess: () => ({ capabilities: { archive_mutation: false }, online: true }),
}))

vi.mock("@/platform/use-platform", () => ({
  usePlatform: () => ({ kind: "web", isNative: false }),
}))

vi.mock("@/lib/router", () => ({ navigate: mocks.navigate }))

vi.mock("@/lib/api", () => ({
  api: {
    tasks: {
      get: mocks.getTask,
      checkpointRerun: mocks.checkpointRerun,
      create: vi.fn(),
      pause: vi.fn(),
      resume: vi.fn(),
    },
  },
}))

vi.mock("@/components/archive-card", () => ({
  ArchiveCard: ({ archive: item, onCheckpointRerun }: {
    archive: ArchiveItem
    onCheckpointRerun?: () => void
  }) => (
    <button type="button" onClick={onCheckpointRerun}>
      断点续做 {item.title}
    </button>
  ),
}))

vi.mock("@/components/offline-sync-status", () => ({ OfflineSyncStatus: () => null }))

vi.stubGlobal("ResizeObserver", class {
  observe() {}
  disconnect() {}
})

afterEach(cleanup)

beforeEach(() => {
  vi.clearAllMocks()
  mocks.refresh.mockResolvedValue(undefined)
  mocks.checkpointRerun.mockResolvedValue(undefined)
})

describe("FilesPage checkpoint rerun", () => {
  it("keeps the library visible after restarting a completed task", async () => {
    mocks.getTask.mockResolvedValue({ status: "completed" })
    render(<FilesPage search="" mediaFilter="all" sourceFilter="all" sort="created_desc" />)

    fireEvent.click(screen.getByRole("button", { name: "断点续做 断点任务" }))

    await waitFor(() => expect(mocks.checkpointRerun).toHaveBeenCalledWith("task-1"))
    expect(mocks.refresh).toHaveBeenCalledWith(true)
    expect(mocks.navigate).not.toHaveBeenCalled()
  })

  it("keeps the library visible when the task is already active", async () => {
    mocks.getTask.mockResolvedValue({ status: "processing" })
    render(<FilesPage search="" mediaFilter="all" sourceFilter="all" sort="created_desc" />)

    fireEvent.click(screen.getByRole("button", { name: "断点续做 断点任务" }))

    await waitFor(() => expect(mocks.refresh).toHaveBeenCalledWith(true))
    expect(mocks.checkpointRerun).not.toHaveBeenCalled()
    expect(mocks.navigate).not.toHaveBeenCalled()
  })
})
