/** @vitest-environment jsdom */
import { act, cleanup, renderHook, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { useResultViewer } from "./use-result-viewer"

const mocks = vi.hoisted(() => {
  const archive = {
    path: "/library/fixture", title: "Fixture", task_id: null, processing: false,
    has_video: false, media_file: "/library/fixture/source.wav",
    metadata: { title: "Fixture", source_url: "https://example.com/source" },
  }
  return {
    archive,
    archives: [archive],
    adapter: { saveTextFile: vi.fn(), openExternal: vi.fn() },
    repository: { get: vi.fn(), readFile: vi.fn(), listFiles: vi.fn(() => []) },
    refresh: vi.fn(), updatePrefs: vi.fn(), prefs: {},
    saved: vi.fn(() => ({ activeTab: "summary", mediaTime: 12 })),
    updateMediaTime: vi.fn(), updateActiveTab: vi.fn(),
    write: vi.fn(), rename: vi.fn(),
    taskGet: vi.fn(), timeline: vi.fn(),
    renameSpeaker: vi.fn(),
    access: { online: true },
    sse: vi.fn(),
  }
})
vi.mock("@/platform/use-platform", () => ({ usePlatform: () => mocks.adapter }))
vi.mock("@/repositories/archive-repository", () => ({ createArchiveRepository: () => mocks.repository }))
vi.mock("@/hooks/use-app-access-context", () => ({ useAppAccess: () => ({ capabilities: {}, online: mocks.access.online }) }))
vi.mock("@/hooks/use-archives", () => ({ useArchives: () => ({ archives: mocks.archives, refresh: mocks.refresh }) }))
vi.mock("@/hooks/use-preferences", () => ({ usePreferences: () => ({ prefs: mocks.prefs, update: mocks.updatePrefs }) }))
vi.mock("@/hooks/use-view-position", () => ({ useViewPosition: () => ({ getSavedPosition: mocks.saved, updateMediaTime: mocks.updateMediaTime, updateActiveTab: mocks.updateActiveTab }) }))
vi.mock("@/hooks/use-task-sse", () => ({ useTaskSSE: (...args: unknown[]) => mocks.sse(...args) }))
vi.mock("@/lib/api", () => ({
  api: {
    filesystem: { mediaUrl: (value: string) => `media:${value}`, write: mocks.write },
    archives: { rename: mocks.rename },
    voiceprints: { renameTaskSpeaker: mocks.renameSpeaker },
    tasks: { get: mocks.taskGet, timeline: mocks.timeline },
  },
}))

const srt = "1\n00:00:00,000 --> 00:00:02,000\n[Speaker 1] Hello\n"

beforeEach(() => {
  vi.clearAllMocks()
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }))
  mocks.repository.get.mockResolvedValue(mocks.archive)
  mocks.repository.readFile.mockImplementation(async (path: string) => {
    if (path.endsWith("summary.md")) return "# Summary"
    if (path.endsWith("mindmap.md")) return "# Map\n- First"
    if (path.endsWith("transcript.srt")) return srt
    return ""
  })
  mocks.rename.mockResolvedValue({ success: true })
  mocks.write.mockResolvedValue({ success: true })
  mocks.access.online = true
  mocks.renameSpeaker.mockReset()
  mocks.taskGet.mockResolvedValue({ id: "task-1", status: "completed", result: { output_dir: mocks.archive.path }, error: null })
  mocks.timeline.mockResolvedValue({ events: [] })
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe("result viewer state", () => {
  it("loads transcript, summary, mindmap and media through the archive repository", async () => {
    const { result } = renderHook(() => useResultViewer({ archivePath: mocks.archive.path }))
    await waitFor(() => expect(result.current.summary).toBe("# Summary"))
    expect(result.current.mindmap).toBe("# Map\n- First")
    expect(result.current.subtitles).toHaveLength(1)
    expect(result.current.mediaUrl).toBe("media:/library/fixture/source.wav")
    expect(result.current.activeTab).toBe("summary")
    expect(result.current.sourceHref).toBe("https://example.com/source")
  })

  it("applies SSE file updates and saves an edited title", async () => {
    const { result } = renderHook(() => useResultViewer({ archivePath: mocks.archive.path }))
    await waitFor(() => expect(result.current.displayTitle).toBe("Fixture"))
    mocks.repository.readFile.mockResolvedValue("# Updated summary")
    await act(async () => {
      const handlers = mocks.sse.mock.calls.at(-1)?.[1] as { onFileReady: (event: unknown) => void }
      handlers.onFileReady({ file: "summary.md", path: "/library/fixture/summary.md" })
    })
    await waitFor(() => expect(result.current.summary).toBe("# Updated summary"))
    act(() => result.current.setTitleDraft("Edited title"))
    await act(async () => { await result.current.commitTitle() })
    expect(mocks.rename).toHaveBeenCalledWith("/library/fixture", "Edited title")
    expect(result.current.displayTitle).toBe("Edited title")
  })

  it("waits for pending subtitle edits to be saved before renaming speakers", async () => {
    const { result } = renderHook(() => useResultViewer({ archivePath: mocks.archive.path, taskId: "task-1" }))
    await waitFor(() => expect(result.current.subtitles).toHaveLength(1))
    act(() => result.current.setTranscriptEditing(true))
    await act(async () => { await result.current.handleRenameSpeaker("Speaker 1", "李华") })
    expect(mocks.renameSpeaker).not.toHaveBeenCalled()
    act(() => {
      result.current.setTranscriptEditing(false)
      result.current.setSubtitles([{ ...result.current.subtitles[0], text: "Edited draft" }])
    })
    await act(async () => { await result.current.handleRenameSpeaker("Speaker 1", "李华") })
    expect(result.current.hasUnsavedTranscript).toBe(true)
    expect(mocks.renameSpeaker).not.toHaveBeenCalled()
  })

  it("serializes manual renames until the file save finishes", async () => {
    let finishRename!: (value: unknown) => void
    let finishSave!: (value: unknown) => void
    mocks.renameSpeaker.mockImplementation(() => new Promise((resolve) => { finishRename = resolve }))
    mocks.write.mockImplementationOnce(() => new Promise((resolve) => { finishSave = resolve }))
    const { result } = renderHook(() => useResultViewer({ archivePath: mocks.archive.path, taskId: "task-1" }))
    await waitFor(() => expect(result.current.subtitles).toHaveLength(1))
    let pending!: Promise<void>
    act(() => { pending = result.current.handleRenameSpeaker("Speaker 1", "李华") })
    expect(result.current.renamingSpeaker).toBe(true)
    await act(async () => {
      await result.current.handleRenameSpeaker("Speaker 1", "张明")
    })
    expect(mocks.renameSpeaker).toHaveBeenCalledOnce()
    await act(async () => {
      finishRename({ status: "renamed", person_name: "李华" })
    })
    expect(result.current.renamingSpeaker).toBe(true)
    await act(async () => { await result.current.handleRenameSpeaker("李华", "张明") })
    expect(mocks.renameSpeaker).toHaveBeenCalledOnce()
    await act(async () => {
      finishSave({ success: true })
      await pending
    })
    expect(result.current.renamingSpeaker).toBe(false)
    expect(result.current.hasUnsavedTranscript).toBe(false)
    expect(result.current.subtitles[0].speaker).toBe("李华")
  })

  it("blocks additional renames while a name conflict is open and resolving", async () => {
    mocks.renameSpeaker.mockResolvedValueOnce({ status: "conflict", conflict_person_id: "person-2", conflict_person_name: "李华", conflict_sample_count: 2 })
    const { result } = renderHook(() => useResultViewer({ archivePath: mocks.archive.path, taskId: "task-1" }))
    await waitFor(() => expect(result.current.subtitles).toHaveLength(1))
    await act(async () => { await result.current.handleRenameSpeaker("Speaker 1", "李华") })
    expect(result.current.mergeInfo).not.toBeNull()
    await act(async () => { await result.current.handleRenameSpeaker("Speaker 1", "张明") })
    expect(mocks.renameSpeaker).toHaveBeenCalledOnce()
    let finishMerge!: (value: unknown) => void
    mocks.renameSpeaker.mockImplementationOnce(() => new Promise((resolve) => { finishMerge = resolve }))
    let pending!: Promise<void>
    act(() => { pending = result.current.resolveMerge("merge") })
    expect(result.current.renamingSpeaker).toBe(true)
    await act(async () => {
      await result.current.handleRenameSpeaker("Speaker 1", "张明")
      await result.current.resolveMerge("cancel")
    })
    expect(mocks.renameSpeaker).toHaveBeenCalledTimes(2)
    expect(result.current.mergeInfo).not.toBeNull()
    await act(async () => {
      finishMerge({ status: "merged", person_name: "李华" })
      await pending
    })
    expect(result.current.renamingSpeaker).toBe(false)
    expect(result.current.mergeInfo).toBeNull()
    expect(result.current.subtitles[0].speaker).toBe("李华")
  })
})
