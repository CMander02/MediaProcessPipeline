/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { api } from "@/lib/api"
import { MediaRetentionDialog } from "./media-retention-dialog"

vi.mock("@/lib/api", () => ({ api: { mediaRetention: { preview: vi.fn(), apply: vi.fn() } } }))
afterEach(() => { cleanup(); vi.clearAllMocks() })
const entry = { path: "working.wav", role: "working", bytes: 1024, delete: true,
  reason: "按所选策略清理", recovery: "重新生成依赖：source.mp4" }
const all = { path: "D:/archive", policy: "all" as const, entries: [{ ...entry, delete: false }],
  reclaimable_bytes: 0, protected_reason: "" }

describe("media retention preview", () => {
  it("requires a new preview after changing policy and applies only listed files", async () => {
    vi.mocked(api.mediaRetention.preview).mockResolvedValueOnce(all).mockResolvedValueOnce({
      ...all, policy: "playback", entries: [entry], reclaimable_bytes: 1024,
    })
    vi.mocked(api.mediaRetention.apply).mockResolvedValue({ ...all, policy: "playback",
      entries: [], cleaned: [entry], reclaimed_bytes: 1024 })
    const applied = vi.fn()
    render(<MediaRetentionDialog archive={{ path: "D:/archive", title: "归档" }} onClose={vi.fn()} onApplied={applied} />)
    await screen.findByText("working.wav")
    fireEvent.change(screen.getByLabelText("保留策略"), { target: { value: "playback" } })
    expect((screen.getByRole("button", { name: "清理所列 0 个文件" }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(screen.getByRole("button", { name: "预览" }))
    await screen.findByText("重新生成依赖：source.mp4。")
    fireEvent.click(screen.getByRole("button", { name: "清理所列 1 个文件" }))
    await waitFor(() => expect(applied).toHaveBeenCalledOnce())
    expect(api.mediaRetention.apply).toHaveBeenCalledWith("D:/archive", "playback", ["working.wav"])
    expect(screen.getByText("已回收 1,024 B，清理 1 个文件")).toBeTruthy()
  })

  it("shows protection reasons and keeps execution disabled", async () => {
    vi.mocked(api.mediaRetention.preview).mockResolvedValue({ ...all, protected_reason: "归档正在上传" })
    render(<MediaRetentionDialog archive={{ path: "D:/archive", title: "归档" }} onClose={vi.fn()} onApplied={vi.fn()} />)
    await screen.findByText("归档正在上传")
    expect((screen.getByRole("button", { name: "清理所列 0 个文件" }) as HTMLButtonElement).disabled).toBe(true)
    expect(api.mediaRetention.apply).not.toHaveBeenCalled()
  })
})
