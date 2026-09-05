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

export function collectLegacyMindmapChapters(tree: TranscriptTocNode | null): TranscriptTocNode[] | null {
  if (!tree) return null
  const nodes: TranscriptTocNode[] = []
  const walk = (node: TranscriptTocNode) => {
    if (typeof node.start === "number" && Number.isFinite(node.start)) {
      const title = cleanChapterTitle(node.title)
      if (title) nodes.push({ title, start: node.start, end: node.end })
    }
    node.children?.forEach(walk)
  }
  walk(tree)
  return nodes.length > 0 ? nodes.toSorted((left, right) => (left.start ?? 0) - (right.start ?? 0)) : null
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
