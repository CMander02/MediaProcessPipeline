import { describe, expect, it } from "vitest"

import { buildChapterTree, chapterPathAt, flattenChapterTree } from "./result-chapters"

const chapters = [
  { title: "Why work on AI?", start: 0 },
  { title: "Scaling breakthroughs", start: 128 },
  { title: "Early days", start: 210 },
]

const mindmap = {
  title: "Mindmap",
  children: [
    {
      title: "Why work on AI?",
      start: 0,
      children: [
        { title: "Jared shifted to AI", start: 0, end: 14 },
        { title: "Dario convinced Jared", start: 14, end: 34 },
      ],
    },
    {
      // The mindmap groups by topic: this point is spoken during "Early days".
      title: "Scaling breakthroughs",
      start: 128,
      children: [
        {
          title: "Constitution idea",
          start: 240,
          end: 300,
          children: [
            { title: "Multiple choice", start: 250, end: 270 },
            { title: "Out of range", start: 900, end: 910 },
          ],
        },
      ],
    },
  ],
}

describe("buildChapterTree", () => {
  it("nests timed mindmap points under the chapter they are spoken in", () => {
    const tree = buildChapterTree(chapters, mindmap, 400)
    expect(tree.map((node) => [node.title, node.start, node.end])).toEqual([
      ["Why work on AI?", 0, 128],
      ["Scaling breakthroughs", 128, 210],
      ["Early days", 210, 400],
    ])
    expect(tree[0].children?.map((node) => node.title)).toEqual(["Jared shifted to AI", "Dario convinced Jared"])
    expect(tree[1].children).toBeUndefined()
    expect(tree[2].children?.[0].title).toBe("Constitution idea")
    expect(tree[2].children?.[0].children?.map((node) => node.title)).toEqual(["Multiple choice"])
  })

  it("falls back to the mindmap's first level when there is no chapter list", () => {
    const tree = buildChapterTree(null, mindmap, 400)
    expect(tree.map((node) => node.title)).toEqual(["Why work on AI?", "Scaling breakthroughs"])
  })

  it("keeps a flat list when there is no mindmap", () => {
    expect(buildChapterTree(chapters, null, 400).every((node) => !node.children)).toBe(true)
  })
})

describe("chapterPathAt", () => {
  const tree = buildChapterTree(chapters, mindmap, 400)

  it("returns chapter, section and sub-section at a time", () => {
    expect(chapterPathAt(tree, 255).map((node) => node.title)).toEqual(["Early days", "Constitution idea", "Multiple choice"])
    expect(chapterPathAt(tree, 20).map((node) => node.title)).toEqual(["Why work on AI?", "Dario convinced Jared"])
  })

  it("stops at the chapter between sections", () => {
    expect(chapterPathAt(tree, 60).map((node) => node.title)).toEqual(["Why work on AI?"])
  })

  it("lists every node with its depth", () => {
    expect(flattenChapterTree(tree).map(({ node, depth }) => `${depth}:${node.title}`)).toContain("2:Multiple choice")
  })
})
