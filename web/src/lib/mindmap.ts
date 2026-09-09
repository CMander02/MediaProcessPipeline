const LEGACY_MINDMAP_TIME_RE = /\s*\[(?:\d{1,2}:)?\d{1,2}:\d{2}(?:[,.]\d{1,3})?(?:\s*(?:-|–|—|-->)\s*(?:\d{1,2}:)?\d{1,2}:\d{2}(?:[,.]\d{1,3})?)?\]\s*$/
const MINDMAP_LIST_LINE_RE = /^(\s*)[-*]\s+(.+?)\s*$/
const MINDMAP_HEADING_LINE_RE = /^(#{2,6})\s+(.+?)\s*$/

export function sanitizeMindmapMarkdown(markdown: string): string {
  return markdown
    .split("\n")
    .map((line) => line.replace(LEGACY_MINDMAP_TIME_RE, ""))
    .join("\n")
}

/** Render branches as headings and leaf nodes as regular-weight body items. */
export function mindmapMarkdownForReading(markdown: string): string {
  const lines = sanitizeMindmapMarkdown(markdown).split("\n")
  const nodes = lines.map((line) => {
    const listMatch = line.match(MINDMAP_LIST_LINE_RE)
    if (listMatch) {
      return {
        depth: Math.floor(listMatch[1].replace(/\t/g, "  ").length / 2),
        text: listMatch[2],
      }
    }
    const headingMatch = line.match(MINDMAP_HEADING_LINE_RE)
    if (headingMatch) {
      return { depth: headingMatch[1].length - 2, text: headingMatch[2] }
    }
    return null
  })

  return lines.map((line, index) => {
    const node = nodes[index]
    if (!node) return line
    const nextNode = nodes.slice(index + 1).find(Boolean)
    const hasChildren = Boolean(nextNode && nextNode.depth > node.depth)
    if (!hasChildren) return `- ${node.text}`
    return `${"#".repeat(Math.min(6, node.depth + 2))} ${node.text}`
  }).join("\n")
}
