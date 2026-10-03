import { useMemo } from "react"
import { parseSummaryMarkdown } from "@/lib/markdown"
import { ScrollArea } from "@/components/ui/scroll-area"
import { MarkdownRenderer } from "./markdown-renderer"

interface SummaryTabProps {
  content: string
}

export function SummaryTab({ content }: SummaryTabProps) {
  const parsed = useMemo(() => parseSummaryMarkdown(content), [content])
  const displayContent = useMemo(() => {
    const keyFacts = parsed.keyFacts.map((fact) => `- ${fact}`).join("\n")
    // Opening paragraph, then the key points, then the sections (内容脉络 and the like).
    const firstHeading = parsed.displayBody.search(/^#{2,6}\s/m)
    const lead = firstHeading < 0 ? parsed.displayBody : parsed.displayBody.slice(0, firstHeading)
    const sections = firstHeading < 0 ? "" : parsed.displayBody.slice(firstHeading)
    return [
      lead.trim(),
      keyFacts ? `## 核心要点\n\n${keyFacts}` : "",
      sections.trim(),
    ].filter(Boolean).join("\n\n")
  }, [parsed])

  if (!content) {
    return (
      <p className="text-sm text-muted-foreground text-center py-8">无摘要数据</p>
    )
  }

  return (
    <ScrollArea className="h-full">
      <div className="p-4">
        <article className="prose prose-sm dark:prose-invert max-w-none">
          <MarkdownRenderer highlight>{displayContent}</MarkdownRenderer>
        </article>
      </div>
    </ScrollArea>
  )
}
