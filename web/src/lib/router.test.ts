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
  it("round-trips page, search, filters and sort", () => {
    const state = { search: "Ryan Greenblatt", mediaFilter: "video" as const, sourceFilter: "bilibili" as const, sort: "title_asc" as const, page: 3 }
    const hash = libraryHashFromState(state)
    expect(libraryStateFromParams(parseHash(hash).params)).toEqual(state)
  })

  it("omits defaults so the plain library stays #/files", () => {
    expect(libraryHashFromState({ search: "", mediaFilter: "all", sourceFilter: "all", sort: "created_desc", page: 1 })).toBe("#/files")
    expect(buildHash("backend", { tab: "overview", q: "" })).toBe("#/backend?tab=overview")
  })

  it("ignores unknown or invalid values", () => {
    expect(libraryStateFromParams({ page: "-4", media: "podcast", src: "myspace", sort: "random" })).toEqual({
      search: "", mediaFilter: "all", sourceFilter: "all", sort: "created_desc", page: 1,
    })
  })
})
