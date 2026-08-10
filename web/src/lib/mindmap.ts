const LEGACY_MINDMAP_TIME_RE = /\s*\[(?:\d{1,2}:)?\d{1,2}:\d{2}(?:[,.]\d{1,3})?(?:\s*(?:-|–|—|-->)\s*(?:\d{1,2}:)?\d{1,2}:\d{2}(?:[,.]\d{1,3})?)?\]\s*$/
const MINDMAP_LIST_LINE_RE = /^(\s*)[-*]\s+(.+?)\s*$/

export function sanitizeMindmapMarkdown(markdown: string): string {
  return markdown
    .split("\n")
    .map((line) => line.replace(LEGACY_MINDMAP_TIME_RE, ""))
    .join("\n")
}

/** Convert legacy nested-list mindmaps into a document-oriented heading tree. */
export function mindmapMarkdownForReading(markdown: string): string {
  return sanitizeMindmapMarkdown(markdown)
    .split("\n")
    .map((line) => {
      const match = line.match(MINDMAP_LIST_LINE_RE)
      if (!match) return line

      const depth = match[1].replace(/\t/g, "  ").length / 2
      const headingLevel = Math.min(6, Math.floor(depth) + 2)
      return `${"#".repeat(headingLevel)} ${match[2]}`
    })
    .join("\n")
}
