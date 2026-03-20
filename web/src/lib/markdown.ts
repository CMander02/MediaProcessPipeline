/**
 * Parse summary.md: extract YAML frontmatter, body sections, markmap fenced block.
 */

export interface ParsedSummary {
  frontmatter: Record<string, string>
  body: string
  markmapBlock: string | null
  sections: { title: string; content: string }[]
  keyFacts: string[]
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

  // Extract markmap fenced block
  let markmapBlock: string | null = null
  const mmMatch = body.match(/```markmap\n([\s\S]*?)```/)
  if (mmMatch) {
    markmapBlock = mmMatch[1].trim()
    body = body.replace(mmMatch[0], "").trim()
  }

  // Extract sections by ## headings
  const sections: { title: string; content: string }[] = []
  const sectionRegex = /^## (.+)$/gm
  let lastIndex = 0
  let lastTitle = ""
  let match: RegExpExecArray | null

  while ((match = sectionRegex.exec(body)) !== null) {
    if (lastTitle) {
      sections.push({
        title: lastTitle,
        content: body.slice(lastIndex, match.index).trim(),
      })
    }
    lastTitle = match[1]
    lastIndex = match.index + match[0].length
  }
  if (lastTitle) {
    sections.push({
      title: lastTitle,
      content: body.slice(lastIndex).trim(),
    })
  }

  // Extract key facts from bullet list under matching section
  const keyFacts: string[] = []
  const factsSection = sections.find(
    (s) =>
      s.title.includes("关键事实") ||
      s.title.toLowerCase().includes("key fact") ||
      s.title.includes("核心要点") ||
      s.title.includes("要点"),
  )
  if (factsSection) {
    const bulletRegex = /^[-*]\s+(.+)$/gm
    let bm: RegExpExecArray | null
    while ((bm = bulletRegex.exec(factsSection.content)) !== null) {
      keyFacts.push(bm[1].trim())
    }
  }

  return { frontmatter, body, markmapBlock, sections, keyFacts }
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
