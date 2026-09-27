import { type TranscriptTocNode } from "@/components/result/transcript-tab"
import { asRecord } from "./result-metadata"

const CHAPTER_TIME_SUFFIX_RE = /\s*\[(?:\d{1,2}:)?\d{1,2}:\d{2}(?:[,.]\d{1,3})?(?:\s*(?:-|–|—|-->)\s*(?:\d{1,2}:)?\d{1,2}:\d{2}(?:[,.]\d{1,3})?)?\]\s*$/

function cleanChapterTitle(value: unknown): string {
  return typeof value === "string" ? value.replace(CHAPTER_TIME_SUFFIX_RE, "").trim() : ""
}

export function parseChapterTimeline(content: string): TranscriptTocNode[] | null {
  if (!content) return null
  try {
    const payload = asRecord(JSON.parse(content))
    const timeline = Array.isArray(payload?.timeline) ? payload.timeline : []
    const seen = new Set<string>()
    const nodes = timeline.flatMap((value) => {
      const item = asRecord(value)
      const start = Number(item?.start)
      const title = cleanChapterTitle(item?.title)
      if (!Number.isFinite(start) || start < 0 || !title) return []
      const key = `${start}:${title}`
      if (seen.has(key)) return []
      seen.add(key)
      return [{ title, start } satisfies TranscriptTocNode]
    })
    return nodes.length > 0 ? nodes.toSorted((left, right) => (left.start ?? 0) - (right.start ?? 0)) : null
  } catch {
    return null
  }
}

export function completeChapterRanges(nodes: TranscriptTocNode[] | null, duration: number): TranscriptTocNode[] {
  if (!nodes?.length) return []
  return nodes.map((node, index) => {
    const nextStart = nodes[index + 1]?.start
    const inferredEnd = typeof nextStart === "number" ? nextStart : duration > 0 ? duration : undefined
    return {
      ...node,
      title: cleanChapterTitle(node.title),
      end: typeof node.end === "number" ? node.end : inferredEnd,
    }
  })
}

const byStart = (left: TranscriptTocNode, right: TranscriptTocNode) => (left.start ?? 0) - (right.start ?? 0)
const isTimed = (node: TranscriptTocNode) => typeof node.start === "number" && Number.isFinite(node.start)

/** Give each node an end: its own, else the next sibling's start, else the parent's end. */
function withEnds(nodes: TranscriptTocNode[], parentEnd: number | undefined): TranscriptTocNode[] {
  return nodes.map((node, index) => {
    const next = nodes[index + 1]?.start
    const end = typeof node.end === "number" ? node.end : typeof next === "number" ? next : parentEnd
    return { ...node, title: cleanChapterTitle(node.title), end, children: undefined }
  })
}

/**
 * Chapters with their sections and sub-sections, in playback order.
 * Chapters come from the source or summary timeline (else the mindmap's first level); the
 * mindmap's timed second and third levels become sections, placed under the chapter their
 * start time falls in (the mindmap groups by topic, the transcript runs by time).
 */
export function buildChapterTree(
  chapters: TranscriptTocNode[] | null,
  mindmap: TranscriptTocNode | null,
  duration: number,
): TranscriptTocNode[] {
  const firstLevel = chapters?.length
    ? chapters
    : (mindmap?.children ?? []).filter(isTimed).map((node) => ({ title: node.title, start: node.start, end: node.end }))
  const top = completeChapterRanges(firstLevel.length ? firstLevel.toSorted(byStart) : null, duration)
  const sections = (mindmap?.children ?? []).flatMap((node) => (node.children ?? []).filter(isTimed))
  if (!sections.length) return top
  return top.map((chapter) => {
    const start = chapter.start ?? 0
    const end = chapter.end ?? Number.POSITIVE_INFINITY
    const inside = sections
      .filter((node) => node.start! >= start && node.start! < end)
      .filter((node) => cleanChapterTitle(node.title) !== chapter.title)
      .toSorted(byStart)
    if (!inside.length) return chapter
    const children = withEnds(inside, chapter.end).map((section, index) => {
      const original = inside[index]
      const sectionEnd = section.end ?? Number.POSITIVE_INFINITY
      const subsections = (original.children ?? [])
        .filter(isTimed)
        .filter((node) => node.start! >= section.start! && node.start! < sectionEnd)
        .toSorted(byStart)
      return subsections.length ? { ...section, children: withEnds(subsections, section.end) } : section
    })
    return { ...chapter, children }
  })
}

/** The chapter, section and sub-section playing at `seconds`, outermost first. */
export function chapterPathAt(tree: TranscriptTocNode[], seconds: number): TranscriptTocNode[] {
  const path: TranscriptTocNode[] = []
  let level: TranscriptTocNode[] | undefined = tree
  while (level?.length) {
    const node: TranscriptTocNode | undefined = level.findLast((item) => isTimed(item)
      && item.start! <= seconds && (typeof item.end !== "number" || seconds < item.end))
    if (!node) break
    path.push(node)
    level = node.children
  }
  return path
}

/** Every node of the tree with its depth (0 = chapter), in playback order. */
export function flattenChapterTree(tree: TranscriptTocNode[]): Array<{ node: TranscriptTocNode; depth: number }> {
  const out: Array<{ node: TranscriptTocNode; depth: number }> = []
  const walk = (nodes: TranscriptTocNode[], depth: number) => {
    for (const node of nodes) {
      out.push({ node, depth })
      if (node.children?.length) walk(node.children, depth + 1)
    }
  }
  walk(tree, 0)
  return out
}

/** Stable identity of a chapter or section, for highlighting and open/closed state. */
export function chapterKey(node: TranscriptTocNode): string {
  return `${node.start ?? "x"}:${node.title}`
}
