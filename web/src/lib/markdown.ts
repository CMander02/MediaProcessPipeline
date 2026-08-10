/**
 * Parse summary.md: extract YAML frontmatter, body sections, markmap fenced block.
 */

export interface ParsedSummary {
  frontmatter: Record<string, string>
  body: string
  displayBody: string
  sections: { title: string; content: string; level: number }[]
  keyFacts: string[]
}

const SUMMARY_SECTION_RE = /^(#{2,6})\s+(.+)$/gm

function isTimelineTitle(title: string): boolean {
  const normalized = title.trim().toLowerCase()
  return normalized === "timeline" || normalized === "时间轴" || normalized === "章节时间轴"
}

function isKeyFactsTitle(title: string): boolean {
  const normalized = title.trim().toLowerCase()
  return (
    normalized.includes("关键事实") ||
    normalized.includes("核心要点") ||
    normalized === "要点" ||
    normalized.includes("key fact")
  )
}

/**
 * Parse a summary.md file content
 */
export function parseSummaryMarkdown(content: string): ParsedSummary {
  let frontmatter: Record<string, string> = {}
  let body = content

  // Extract YAML frontmatter
  const fmMatch = content.match(/^---\n([\s\S]*?)\n---\n([\s\S]*)$/)
  if (fmMatch) {
    frontmatter = parseSimpleYaml(fmMatch[1])
    body = fmMatch[2]
  }


  // Extract independently rendered H2-H6 sections. Summary files historically
  // used H3 for Key Facts and Timeline, so all content heading levels matter.
  const sectionMatches = Array.from(body.matchAll(SUMMARY_SECTION_RE))
  const sections = sectionMatches.map((heading, index) => ({
    title: heading[2].trim(),
    level: heading[1].length,
    content: body.slice(
      (heading.index ?? 0) + heading[0].length,
      sectionMatches[index + 1]?.index ?? body.length,
    ).trim(),
  }))

  // Extract key facts from bullet list under matching section
  const keyFacts: string[] = []
  const factsSection = sections.find((section) => isKeyFactsTitle(section.title))
  if (factsSection) {
    const bulletRegex = /^[-*]\s+(.+)$/gm
    let bm: RegExpExecArray | null
    while ((bm = bulletRegex.exec(factsSection.content)) !== null) {
      keyFacts.push(bm[1].trim())
    }
  }

  const hiddenSections = sectionMatches
    .map((heading, index) => ({
      start: heading.index ?? 0,
      end: sectionMatches[index + 1]?.index ?? body.length,
      title: heading[2].trim(),
    }))
    .filter((section) => isTimelineTitle(section.title) || (keyFacts.length > 0 && isKeyFactsTitle(section.title)))

  let displayBody = body
  for (const section of hiddenSections.toReversed()) {
    displayBody = `${displayBody.slice(0, section.start).trimEnd()}\n\n${displayBody.slice(section.end).trimStart()}`
  }
  displayBody = displayBody
    .replace(/^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/gm, "")
    .replace(/\n{3,}/g, "\n\n")

  return { frontmatter, body, displayBody: displayBody.trim(), sections, keyFacts }
}

function parseSimpleYaml(yaml: string): Record<string, string> {
  const result: Record<string, string> = {}
  for (const line of yaml.split("\n")) {
    const idx = line.indexOf(":")
    if (idx > 0) {
      const key = line.slice(0, idx).trim()
      let val = line.slice(idx + 1).trim()
      // Remove surrounding quotes
      if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
        val = val.slice(1, -1)
      }
      result[key] = val
    }
  }
  return result
}
