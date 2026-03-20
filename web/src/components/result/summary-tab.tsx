import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import rehypeHighlight from "rehype-highlight"
import { useMemo } from "react"
import { parseSummaryMarkdown } from "@/lib/markdown"
import { KeyFactsCards } from "./key-facts-cards"
import { ScrollArea } from "@/components/ui/scroll-area"

interface SummaryTabProps {
  content: string
}

export function SummaryTab({ content }: SummaryTabProps) {
  const parsed = useMemo(() => parseSummaryMarkdown(content), [content])

  if (!content) {
    return (
      <p className="text-sm text-muted-foreground text-center py-8">无摘要数据</p>
    )
  }

  return (
    <ScrollArea className="h-full">
      <div className="p-4 space-y-6">
        {/* Key Facts cards */}
        {parsed.keyFacts.length > 0 && (
          <KeyFactsCards facts={parsed.keyFacts} />
        )}

        {/* Rendered markdown body */}
        <article className="prose prose-sm dark:prose-invert max-w-none">
          <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
            {parsed.body}
          </ReactMarkdown>
        </article>
      </div>
    </ScrollArea>
  )
}
