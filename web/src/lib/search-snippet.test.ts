import { describe, expect, it } from "vitest"

import { matchParts, snippetAround } from "./search-snippet"

describe("snippetAround", () => {
  it("keeps short text whole", () => {
    expect(snippetAround("Why are we working on AI?", "AI")).toBe("Why are we working on AI?")
  })

  it("cuts long text around the match", () => {
    const text = `${"前".repeat(100)}人工智能${"后".repeat(100)}`
    const snippet = snippetAround(text, "人工智能", 40)
    expect(snippet.startsWith("…")).toBe(true)
    expect(snippet.endsWith("…")).toBe(true)
    expect(snippet).toContain("人工智能")
    expect(snippet.length).toBe(42)
  })
})

describe("matchParts", () => {
  it("splits out every match, ignoring case", () => {
    expect(matchParts("AI and ai", "ai")).toEqual([
      { text: "AI", match: true },
      { text: " and ", match: false },
      { text: "ai", match: true },
    ])
    expect(matchParts("abc", "")).toEqual([{ text: "abc", match: false }])
  })
})
