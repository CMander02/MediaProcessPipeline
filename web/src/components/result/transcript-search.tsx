import { Search, X } from "lucide-react"
import { Input } from "@/components/ui/input"

interface TranscriptSearchProps {
  value: string
  onChange: (value: string) => void
  matchCount: number
}

export function TranscriptSearch({ value, onChange, matchCount }: TranscriptSearchProps) {
  return (
    <div className="relative">
      <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
      <Input
        placeholder="搜索字幕..."
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="pl-8 pr-16 h-8 text-sm"
      />
      {value && (
        <div className="absolute right-2 top-1/2 -translate-y-1/2 flex items-center gap-1.5">
          <span className="text-xs text-muted-foreground">
            {matchCount} 条
          </span>
          <button
            onClick={() => onChange("")}
            className="text-muted-foreground hover:text-foreground"
          >
            <X className="h-3 w-3" />
          </button>
        </div>
      )}
    </div>
  )
}
