import ReactMarkdown, { type Components } from "react-markdown"
import remarkGfm from "remark-gfm"
import remarkMath from "remark-math"
import rehypeKatex from "rehype-katex"
import rehypeHighlight from "rehype-highlight"

interface MarkdownRendererProps {
  children: string
  components?: Components
  highlight?: boolean
}

export function MarkdownRenderer({ children, components, highlight = false }: MarkdownRendererProps) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm, remarkMath]}
      rehypePlugins={highlight ? [rehypeKatex, rehypeHighlight] : [rehypeKatex]}
      components={components}
    >
      {children}
    </ReactMarkdown>
  )
}
