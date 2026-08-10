const SECTION_LINE_RE = /^【(.+?)】$/
const PART_LINE_RE = /^(Part\s+\d+\b.+)$/i
const TIMELINE_LINE_RE = /^((?:(?:\d{1,2}):)?\d{1,2}:\d{2})\s+(.+)$/
const LABELED_LINE_RE = /^([^：\n]{1,12}：)\s*(.+)$/
const X_SOURCE_RE = /^Source:\s+https?:\/\/(?:www\.)?(?:x|twitter)\.com\//im
const X_HANDLE_RE = /^@[A-Za-z0-9_]{1,15}$/
const X_RELATIVE_TIME_RE = /^\d+[smhdw]$/i
const X_TRAILING_METADATA_RE = /^(?:[\d,.]+[KMB]?|Views|\d{1,2}:\d{2}\s+[AP]M\s+·\s+.+)$/i
const X_LOGIN_PROMPT_RE = /^Log in or sign up for X$/i

function alreadyStructured(markdown: string): boolean {
  return /^(?:#{1,6}\s|[-*]\s|\d+\.\s)/m.test(markdown)
}

function formatXThreadMarkdown(content: string): string {
  const allLines = content.replace(/\r\n?/g, "\n").split("\n")
  const loginPrompt = allLines.findIndex((line) => X_LOGIN_PROMPT_RE.test(line.trim()))
  const lines = (loginPrompt >= 0 ? allLines.slice(0, loginPrompt) : allLines)
    .map((line) => line.trimEnd())

  const postStarts: number[] = []
  for (let index = 0; index < lines.length - 1; index += 1) {
    if (lines[index].trim() && X_HANDLE_RE.test(lines[index + 1].trim())) {
      postStarts.push(index)
    }
  }
  if (postStarts.length === 0) return content

  const sections = [lines.slice(0, postStarts[0]).join("\n").trim()]
  for (let index = 0; index < postStarts.length; index += 1) {
    const start = postStarts[index]
    const end = postStarts[index + 1] ?? lines.length
    const author = lines[start].trim()
    const handle = lines[start + 1].trim()
    let bodyStart = start + 2
    let time = ""
    if (X_RELATIVE_TIME_RE.test(lines[bodyStart]?.trim() ?? "")) {
      time = lines[bodyStart].trim()
      bodyStart += 1
    }

    const bodyLines = lines.slice(bodyStart, end)
    while (
      bodyLines.length > 0 &&
      (!bodyLines.at(-1)?.trim() || X_TRAILING_METADATA_RE.test(bodyLines.at(-1)?.trim() ?? ""))
    ) {
      bodyLines.pop()
    }

    const byline = `${handle}${time ? ` · ${time}` : ""}`
    const body = bodyLines.map((line) => line.trim()).filter(Boolean).join("\n\n")
    sections.push(`**${author}**  \n${byline}${body ? `\n\n${body}` : ""}`)
  }

  return sections.filter(Boolean).join("\n\n")
}

/** Improve plain RSS descriptions while leaving rich Markdown untouched. */
export function formatSourceDescriptionMarkdown(content: string): string {
  if (!content) return content
  if (X_SOURCE_RE.test(content)) return formatXThreadMarkdown(content)
  if (alreadyStructured(content)) return content

  return content
    .split("\n")
    .map((rawLine) => {
      const line = rawLine.trimEnd()
      const section = line.trim().match(SECTION_LINE_RE)
      if (section) return `## ${section[1]}`

      const part = line.trim().match(PART_LINE_RE)
      if (part) return `### ${part[1]}`

      const timeline = line.trim().match(TIMELINE_LINE_RE)
      if (timeline) return `- **${timeline[1]}** ${timeline[2]}`

      const labeled = line.trim().match(LABELED_LINE_RE)
      if (labeled) return `**${labeled[1]}** ${labeled[2]}`

      return line
    })
    .join("\n")
}
