/** @vitest-environment jsdom */
import { describe, expect, it } from "vitest"

import { libraryHashFromState, libraryStateFromParams } from "@/hooks/use-library-controls"
import { buildHash, parseHash } from "./router"

describe("parseHash", () => {
  it("keeps query params for the library and backend pages", () => {
    expect(parseHash("#/files?page=3&src=bilibili")).toEqual({ page: "files", params: { page: "3", src: "bilibili" } })
    expect(parseHash("#/backend?tab=logs&q=da008499").params).toEqual({ tab: "logs", q: "da008499" })
    expect(parseHash("").page).toBe("files")
  })

  it("still resolves result routes", () => {
    const route = parseHash("#/result/archive?path=D%3A%5Cx&taskId=t1")
    expect(route.page).toBe("result")
    expect(route.resultType).toBe("archive")
    expect(route.resultId).toBe("D:\\x")
    expect(route.taskId).toBe("t1")
  })
})

describe("library state in the URL", () => {
  const defaults = {
    search: "", media: [], sources: [], statuses: [], duplicates: false, sort: "created_desc" as const, page: 1,
  }

  it("round-trips search, several values per filter, the duplicates switch, sort and page", () => {
    const state = {
      search: "Ryan Greenblatt",
      media: ["video" as const, "audio" as const],
      sources: ["bilibili" as const, "youtube" as const],
      statuses: ["paused" as const, "failed" as const],
      duplicates: true,
      sort: "title_asc" as const,
      page: 3,
    }
    const hash = libraryHashFromState(state)
    expect(libraryStateFromParams(parseHash(hash).params)).toEqual(state)
  })

  it("omits defaults so the plain library stays #/files", () => {
    expect(libraryHashFromState(defaults)).toBe("#/files")
    expect(buildHash("backend", { tab: "overview", q: "" })).toBe("#/backend?tab=overview")
  })

  it("ignores unknown values and keeps the known ones", () => {
    expect(libraryStateFromParams({ page: "-4", media: "podcast,video", src: "myspace", sort: "random", status: "weird" }))
      .toEqual({ ...defaults, media: ["video"] })
  })

  it("opens old status=duplicates links with the duplicates switch on", () => {
    expect(libraryStateFromParams({ status: "duplicates" })).toEqual({ ...defaults, duplicates: true })
  })
})
