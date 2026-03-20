import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import type { Subtitle } from "@/lib/srt"
import { TranscriptSegment } from "./transcript-segment"
import { TranscriptSearch } from "./transcript-search"
import { ScrollArea } from "@/components/ui/scroll-area"

interface TranscriptTabProps {
  subtitles: Subtitle[]
  currentSegmentIndex: number
  autoScroll: boolean
  onSegmentClick: (subtitle: Subtitle) => void
  onManualScroll: () => void
}

export function TranscriptTab({
  subtitles,
  currentSegmentIndex,
  autoScroll,
  onSegmentClick,
  onManualScroll,
}: TranscriptTabProps) {
  const [searchQuery, setSearchQuery] = useState("")
  const scrollRef = useRef<HTMLDivElement>(null)
  const segmentRefs = useRef<Map<number, HTMLDivElement>>(new Map())
  const isUserScrolling = useRef(false)

  // Filter subtitles by search
  const filteredIndices = useMemo(() => {
    if (!searchQuery) return subtitles.map((_, i) => i)
    const q = searchQuery.toLowerCase()
    return subtitles
      .map((sub, i) => (sub.text.toLowerCase().includes(q) ? i : -1))
      .filter((i) => i >= 0)
  }, [subtitles, searchQuery])

  const matchCount = searchQuery ? filteredIndices.length : 0

  // Auto-scroll to current segment
  useEffect(() => {
    if (!autoScroll || currentSegmentIndex < 0 || isUserScrolling.current) return
    if (searchQuery) return // Don't auto-scroll during search

    const el = segmentRefs.current.get(currentSegmentIndex)
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" })
    }
  }, [currentSegmentIndex, autoScroll, searchQuery])

  const handleScroll = useCallback(() => {
    isUserScrolling.current = true
    onManualScroll()
    // Reset after a brief pause
    setTimeout(() => {
      isUserScrolling.current = false
    }, 200)
  }, [onManualScroll])

  const setSegmentRef = useCallback((index: number, el: HTMLDivElement | null) => {
    if (el) {
      segmentRefs.current.set(index, el)
    } else {
      segmentRefs.current.delete(index)
    }
  }, [])

  return (
    <div className="flex flex-col h-full">
      <div className="px-3 py-2 border-b">
        <TranscriptSearch
          value={searchQuery}
          onChange={setSearchQuery}
          matchCount={matchCount}
        />
      </div>
      <ScrollArea className="flex-1" onScrollCapture={handleScroll}>
        <div ref={scrollRef} className="py-2 space-y-0.5">
          {filteredIndices.length === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-8">
              {searchQuery ? "无匹配结果" : "无字幕数据"}
            </p>
          ) : (
            filteredIndices.map((idx) => (
              <div key={idx} ref={(el) => setSegmentRef(idx, el)}>
                <TranscriptSegment
                  subtitle={subtitles[idx]}
                  isActive={idx === currentSegmentIndex}
                  searchQuery={searchQuery}
                  onClick={() => onSegmentClick(subtitles[idx])}
                />
              </div>
            ))
          )}
        </div>
      </ScrollArea>
    </div>
  )
}
