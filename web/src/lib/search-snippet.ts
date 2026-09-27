/** A window of `text` around the first match of `query`, with an ellipsis where it was cut. */
export function snippetAround(text: string, query: string, width = 90): string {
  if (text.length <= width) return text
  const at = text.toLowerCase().indexOf(query.toLowerCase())
  if (at < 0) return `${text.slice(0, width)}…`
  // Keep a little context before the match and most of the room after it.
  const start = Math.max(0, Math.min(at - Math.floor((width - query.length) / 4), text.length - width))
  const end = start + width
  return `${start > 0 ? "…" : ""}${text.slice(start, end)}${end < text.length ? "…" : ""}`
}

/** Text split into the parts that match `query` (case-insensitively) and the rest. */
export function matchParts(text: string, query: string): Array<{ text: string; match: boolean }> {
  const needle = query.toLowerCase()
  if (!needle) return [{ text, match: false }]
  const haystack = text.toLowerCase()
  const parts: Array<{ text: string; match: boolean }> = []
  let from = 0
  for (let at = haystack.indexOf(needle); at >= 0; at = haystack.indexOf(needle, from)) {
    if (at > from) parts.push({ text: text.slice(from, at), match: false })
    parts.push({ text: text.slice(at, at + needle.length), match: true })
    from = at + needle.length
  }
  if (from < text.length) parts.push({ text: text.slice(from), match: false })
  return parts
}
