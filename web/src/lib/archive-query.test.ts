import { describe, expect, it } from "vitest"
import { queryLocalArchives } from "./archive-query"
import type { ArchiveItem, ArchiveQuery } from "@/repositories/archive-types"
import type { LibraryFolder } from "@/lib/library-types"

const file = (id: string, day: number): ArchiveItem => ({ archive_id: id, path: id, title: id,
  created_at: "2026-09-01", last_processed_at: `2026-09-${day}`, date: "2026-09-01", metadata: {},
  has_video: true, has_audio: false, has_image: false, has_transcript: true, has_summary: true, has_mindmap: false,
  media_file: null, duration_seconds: null, analysis: {} })
const folder: LibraryFolder = { id: "folder", name: "folder", count: 1, created_at: "2026-10-04",
  updated_at: "2026-10-04", last_processed_at: "2026-09-11", source_collection_key: null }
const query: ArchiveQuery = { page: 1, page_size: 2, search: "", media: "all", source: "all",
  sort: "processed_desc", unclassified: true, include_folders: true }
describe("offline mixed library order", () => {
  it("merges folders and files before paging with the same processing time rule", () => {
    const items = [file("old", 10), file("new", 12)]
    const first = queryLocalArchives(items, query, [folder])
    const second = queryLocalArchives(items, { ...query, page: 2 }, [folder])
    expect(first.entries).toEqual([{ kind: "archive", id: "new" }, { kind: "folder", id: "folder" }])
    expect(second.entries).toEqual([{ kind: "archive", id: "old" }])
    expect(first.total).toBe(3)
    expect(first.archive_total).toBe(2)
  })
  it("does not pin old running contents above more recently processed files", () => {
    const older = { ...file("older", 10), processing: true }
    const result = queryLocalArchives([older, file("newer", 12)], { ...query, include_folders: false })
    expect(result.archives.map((item) => item.path)).toEqual(["newer", "older"])
  })
  it("places empty folders last and respects a named folder search", () => {
    const empty = { ...folder, last_processed_at: "", count: 0 }
    expect(queryLocalArchives([file("video", 12)], query, [empty]).entries?.at(-1)).toEqual({ kind: "folder", id: "folder" })
    expect(queryLocalArchives([file("video", 12)], { ...query, search: "folder" }, [folder]).archives).toEqual([])
  })
})
