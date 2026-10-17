import { afterEach, describe, expect, it, vi } from "vitest"
import { api } from "@/lib/api"
import { moveLibraryContents } from "./library-move"

afterEach(() => vi.restoreAllMocks())
describe("content moves", () => {
  it("keeps skipped and failed contents out of the success list", async () => {
    vi.spyOn(api.library, "move").mockResolvedValue({ library_revision: 3, items: [
      { content_key: "a", outcome: "moved" }, { content_key: "b", outcome: "failed", reason: "content_deleting" },
      { content_key: "c", outcome: "skipped", reason: "content_not_found" },
    ] })
    const result = await moveLibraryContents(["a", "b", "c", "a"], "folder")
    expect(api.library.move).toHaveBeenCalledWith(["a", "b", "c"], "folder")
    expect(result.done).toEqual(["a"])
    expect(result.failures).toEqual(["内容正在删除", "内容已不存在"])
  })
  it("moves a selection spanning pages in chunks and preserves earlier successes on network failure", async () => {
    const keys = Array.from({ length: 205 }, (_, i) => `key-${i}`)
    const move = vi.spyOn(api.library, "move").mockImplementation(async (batch) => ({ library_revision: 1,
      items: batch.map((content_key) => ({ content_key, outcome: "moved" as const })) }))
    move.mockImplementationOnce(async (batch) => ({ library_revision: 1, items: batch.map((content_key) => ({ content_key, outcome: "moved" as const })) }))
      .mockRejectedValueOnce(new Error("offline"))
    const result = await moveLibraryContents(keys, null)
    expect(move.mock.calls.map(([batch]) => batch.length)).toEqual([100, 100, 5])
    expect(result.done).toEqual([...keys.slice(0, 100), ...keys.slice(200)])
    expect(result.failures).toEqual(["100 项：offline"])
  })
})
