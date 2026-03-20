import { Badge } from "@/components/ui/badge"
import { Globe, MessageSquare, Hash, Tag } from "lucide-react"

interface AnalysisBadgesProps {
  analysis: {
    language?: string
    content_type?: string
    main_topics?: string[]
    keywords?: string[]
    tone?: string
    speakers_detected?: number
  }
}

export function AnalysisBadges({ analysis }: AnalysisBadgesProps) {
  if (!analysis || Object.keys(analysis).length === 0) return null

  return (
    <div className="space-y-2">
      <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
        内容分析
      </h3>
      <div className="flex flex-wrap gap-1.5">
        {analysis.language && (
          <Badge variant="secondary" className="gap-1">
            <Globe className="w-3 h-3" />
            {analysis.language}
          </Badge>
        )}
        {analysis.content_type && (
          <Badge variant="secondary" className="gap-1">
            <MessageSquare className="w-3 h-3" />
            {analysis.content_type}
          </Badge>
        )}
        {analysis.tone && (
          <Badge variant="secondary" className="gap-1">
            {analysis.tone}
          </Badge>
        )}
        {analysis.speakers_detected != null && analysis.speakers_detected > 0 && (
          <Badge variant="secondary" className="gap-1">
            {analysis.speakers_detected} 人
          </Badge>
        )}
        {analysis.main_topics?.map((topic) => (
          <Badge key={topic} variant="outline" className="gap-1">
            <Tag className="w-3 h-3" />
            {topic}
          </Badge>
        ))}
        {analysis.keywords?.map((kw) => (
          <Badge key={kw} variant="outline" className="gap-1 text-xs">
            <Hash className="w-2.5 h-2.5" />
            {kw}
          </Badge>
        ))}
      </div>
    </div>
  )
}
