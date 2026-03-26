import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import type { Subtitle } from "@/lib/srt"
import { subtitlesToSRT, extractSpeakers } from "@/lib/srt"
import { TranscriptSegment } from "./transcript-segment"
import { TranscriptSearch } from "./transcript-search"
import { ScrollArea } from "@/components/ui/scroll-area"
import { api } from "@/lib/api"

interface TranscriptTabProps {
  subtitles: Subtitle[]
  currentSegmentIndex: number
  autoScroll: boolean
  onSegmentClick: (subtitle: Subtitle) => void
  onManualScroll: () => void
  /** Path to the SRT file for saving edits */
  srtPath?: string
  /** Called when subtitles are modified */
  onSubtitlesChange?: (subtitles: Subtitle[]) => void
}

export function TranscriptTab({
  subtitles,
  currentSegmentIndex,
  autoScroll,
  onSegmentClick,
  onManualScroll,
  srtPath,
  onSubtitlesChange,
}: TranscriptTabProps) {
  const [searchQuery, setSearchQuery] = useState("")
  const [editingIndex, setEditingIndex] = useState<number | null>(null)
  const [isNewInsert, setIsNewInsert] = useState(false) // track if editing a freshly inserted subtitle
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
    if (searchQuery) return
    if (editingIndex !== null) return // Don't auto-scroll during editing

    const el = segmentRefs.current.get(currentSegmentIndex)
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" })
    }
  }, [currentSegmentIndex, autoScroll, searchQuery, editingIndex])

  const handleScroll = useCallback(() => {
    isUserScrolling.current = true
    onManualScroll()
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

  // Save changes to file
  const saveSubtitles = useCallback(async (updated: Subtitle[]) => {
    onSubtitlesChange?.(updated)
    if (srtPath) {
      const srt = subtitlesToSRT(updated)
      try {
        await api.filesystem.write(srtPath, srt)
      } catch (err) {
        console.warn("Failed to save SRT:", err)
      }
    }
  }, [srtPath, onSubtitlesChange])

  const handleEdit = useCallback((index: number, changes: Partial<Subtitle>) => {
    const updated = subtitles.map((sub, i) =>
      i === index ? { ...sub, ...changes } : sub,
    )
    setEditingIndex(null)
    saveSubtitles(updated)
  }, [subtitles, saveSubtitles])

  const handleDelete = useCallback((index: number) => {
    const updated = subtitles.filter((_, i) => i !== index)
    setEditingIndex(null)
    saveSubtitles(updated)
  }, [subtitles, saveSubtitles])

  const handleInsert = useCallback((index: number, position: "above" | "below") => {
    const targetIdx = position === "above" ? index : index + 1
    // Calculate time for new subtitle
    let startTime: number
    let endTime: number
    if (position === "above") {
      const prev = index > 0 ? subtitles[index - 1] : null
      startTime = prev ? Math.round((prev.endTime + subtitles[index].startTime) / 2) : Math.max(0, subtitles[index].startTime - 2000)
      endTime = subtitles[index].startTime
    } else {
      const next = index < subtitles.length - 1 ? subtitles[index + 1] : null
      startTime = subtitles[index].endTime
      endTime = next ? Math.round((subtitles[index].endTime + next.startTime) / 2) : subtitles[index].endTime + 2000
    }

    const newSub: Subtitle = {
      index: targetIdx + 1,
      startTime,
      endTime,
      text: "",
      speaker: subtitles[index]?.speaker,
    }

    const updated = [...subtitles.slice(0, targetIdx), newSub, ...subtitles.slice(targetIdx)]
    onSubtitlesChange?.(updated)
    // Enter edit mode on the new subtitle
    setEditingIndex(targetIdx)
    setIsNewInsert(true)
  }, [subtitles, onSubtitlesChange])

  const handleEditCancel = useCallback((index: number) => {
    if (isNewInsert && editingIndex === index) {
      // Cancel on a freshly inserted subtitle — remove it
      const updated = subtitles.filter((_, i) => i !== index)
      onSubtitlesChange?.(updated)
    }
    setEditingIndex(null)
    setIsNewInsert(false)
  }, [isNewInsert, editingIndex, subtitles, onSubtitlesChange])

  const speakers = useMemo(() => extractSpeakers(subtitles), [subtitles])

  return (
    <div className="flex flex-col h-full">
      <div className="px-3 py-2 border-b">
        <TranscriptSearch
          value={searchQuery}
          onChange={setSearchQuery}
          matchCount={matchCount}
        />
      </div>
      <ScrollArea className="flex-1 min-h-0" onScrollCapture={handleScroll}>
        <div ref={scrollRef} className="py-2 space-y-0.5">
          {filteredIndices.length === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-8">
              {searchQuery ? "无匹配结果" : "无字幕数据"}
            </p>
          ) : (
            filteredIndices.map((idx) => (
              <div key={`${idx}-${subtitles[idx]?.startTime}`} ref={(el) => setSegmentRef(idx, el)}>
                <TranscriptSegment
                  subtitle={subtitles[idx]}
                  isActive={idx === currentSegmentIndex}
                  searchQuery={searchQuery}
                  editing={editingIndex === idx}
                  speakers={speakers}
                  onClick={() => onSegmentClick(subtitles[idx])}
                  onEdit={(changes) => { setIsNewInsert(false); handleEdit(idx, changes) }}
                  onDelete={() => handleDelete(idx)}
                  onInsert={(pos) => handleInsert(idx, pos)}
                  onEditStart={() => { setEditingIndex(idx); setIsNewInsert(false) }}
                  onEditCancel={() => handleEditCancel(idx)}
                />
              </div>
            ))
          )}
        </div>
      </ScrollArea>
    </div>
  )
}
