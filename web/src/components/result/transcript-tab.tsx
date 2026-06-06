import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import type { Subtitle } from "@/lib/srt"
import { subtitlesToSRT, extractSpeakers, findSubtitleIndexAtTime } from "@/lib/srt"
import { TranscriptSegment } from "./transcript-segment"
import { TranscriptSearch } from "./transcript-search"
import { ScrollArea } from "@/components/ui/scroll-area"
import { api } from "@/lib/api"

export interface MindmapTocNode {
  title: string
  start?: number
  end?: number
  children?: MindmapTocNode[]
}

interface TranscriptTabProps {
  subtitles: Subtitle[]
  currentSegmentIndex: number
  autoScroll: boolean
  currentTime?: number
  onSegmentClick: (subtitle: Subtitle) => void
  onManualScroll: () => void
  onTocSeek?: (timeMs: number) => void
  tocTree?: MindmapTocNode | null
  /** Path to the SRT file for saving edits */
  srtPath?: string
  /** Called when subtitles are modified */
  onSubtitlesChange?: (subtitles: Subtitle[]) => void
}

function isTocNodeActive(node: MindmapTocNode, currentTime: number): boolean {
  if (typeof node.start === "number") {
    const end = typeof node.end === "number" ? node.end : node.start + 20
    if (currentTime >= node.start && currentTime <= end) return true
  }
  return Boolean(node.children?.some((child) => isTocNodeActive(child, currentTime)))
}

function TocNode({
  node,
  depth,
  currentTime,
  onSeek,
}: {
  node: MindmapTocNode
  depth: number
  currentTime: number
  onSeek?: (seconds: number) => void
}) {
  const active = isTocNodeActive(node, currentTime)
  const seekable = typeof node.start === "number"
  return (
    <li>
      <button
        type="button"
        disabled={!seekable}
        onClick={() => seekable && onSeek?.(node.start!)}
        className={[
          "w-full rounded px-2 py-1 text-left text-[11px] leading-snug transition-colors",
          seekable ? "hover:bg-muted" : "cursor-default text-muted-foreground",
          active ? "bg-primary/10 text-primary font-medium" : "text-foreground",
        ].join(" ")}
        style={{ paddingLeft: `${8 + depth * 10}px` }}
        title={seekable ? `跳转到 ${Math.floor(node.start! / 60)}:${String(Math.floor(node.start! % 60)).padStart(2, "0")}` : undefined}
      >
        {node.title}
      </button>
      {node.children && node.children.length > 0 && (
        <ul className="mt-0.5 space-y-0.5">
          {node.children.map((child, idx) => (
            <TocNode key={`${child.title}-${idx}`} node={child} depth={depth + 1} currentTime={currentTime} onSeek={onSeek} />
          ))}
        </ul>
      )}
    </li>
  )
}

function TranscriptToc({
  tree,
  currentTime,
  onSeek,
}: {
  tree: MindmapTocNode
  currentTime: number
  onSeek?: (seconds: number) => void
}) {
  const [collapsed, setCollapsed] = useState(false)
  const nodes = tree.children?.length ? tree.children : [tree]
  return (
    <aside
      className={[
        "hidden shrink-0 border-l bg-muted/10 transition-[width] duration-150 md:flex md:flex-col",
        collapsed ? "w-11" : "w-44",
      ].join(" ")}
    >
      <button
        type="button"
        aria-expanded={!collapsed}
        onClick={() => setCollapsed((v) => !v)}
        className={[
          "border-b text-xs font-medium hover:bg-muted/50",
          collapsed
            ? "flex h-full items-start justify-center px-1.5 py-3 [writing-mode:vertical-rl]"
            : "flex items-center justify-between px-2 py-1.5",
        ].join(" ")}
        title={collapsed ? "展开字幕 TOC" : "收起字幕 TOC"}
      >
        <span>{collapsed ? "TOC" : "字幕 TOC"}</span>
        {!collapsed && <span className="text-muted-foreground">收起</span>}
      </button>
      {!collapsed && (
        <ScrollArea className="min-h-0 flex-1">
          <ul className="space-y-0.5 p-2">
            {nodes.map((node, idx) => (
              <TocNode key={`${node.title}-${idx}`} node={node} depth={0} currentTime={currentTime} onSeek={onSeek} />
            ))}
          </ul>
        </ScrollArea>
      )}
    </aside>
  )
}

export function TranscriptTab({
  subtitles,
  currentSegmentIndex,
  autoScroll,
  currentTime = 0,
  onSegmentClick,
  onManualScroll,
  onTocSeek,
  tocTree,
  srtPath,
  onSubtitlesChange,
}: TranscriptTabProps) {
  const [searchQuery, setSearchQuery] = useState("")
  const [editingIndex, setEditingIndex] = useState<number | null>(null)
  const [isNewInsert, setIsNewInsert] = useState(false) // track if editing a freshly inserted subtitle
  const [pendingScrollIndex, setPendingScrollIndex] = useState<number | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const segmentRefs = useRef<Map<number, HTMLDivElement>>(new Map())
  const isUserScrolling = useRef(false)
  const programmaticScroll = useRef(false)

  // Filter subtitles by search
  const filteredIndices = useMemo(() => {
    if (!searchQuery) return subtitles.map((_, i) => i)
    const q = searchQuery.toLowerCase()
    return subtitles
      .map((sub, i) => (sub.text.toLowerCase().includes(q) ? i : -1))
      .filter((i) => i >= 0)
  }, [subtitles, searchQuery])

  const matchCount = searchQuery ? filteredIndices.length : 0

  const scrollToSegment = useCallback((index: number) => {
    const el = segmentRefs.current.get(index)
    if (!el) return false

    programmaticScroll.current = true
    el.scrollIntoView({ behavior: "smooth", block: "center" })
    window.setTimeout(() => {
      programmaticScroll.current = false
    }, 400)
    return true
  }, [])

  // Auto-scroll to current segment
  useEffect(() => {
    if (!autoScroll || currentSegmentIndex < 0 || isUserScrolling.current) return
    if (searchQuery) return
    if (editingIndex !== null) return // Don't auto-scroll during editing

    scrollToSegment(currentSegmentIndex)
  }, [currentSegmentIndex, autoScroll, searchQuery, editingIndex, scrollToSegment])

  useEffect(() => {
    if (pendingScrollIndex === null || searchQuery) return
    if (scrollToSegment(pendingScrollIndex)) {
      setPendingScrollIndex(null)
    }
  }, [filteredIndices.length, pendingScrollIndex, scrollToSegment, searchQuery])

  const handleScroll = useCallback(() => {
    if (programmaticScroll.current) return
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

  const handleTocSeek = useCallback((seconds: number) => {
    const timeMs = Math.max(0, Math.round(seconds * 1000))
    let targetIndex = findSubtitleIndexAtTime(subtitles, timeMs)
    if (targetIndex < 0 && subtitles.length > 0) targetIndex = 0
    if (targetIndex >= 0) {
      if (searchQuery) setSearchQuery("")
      setPendingScrollIndex(targetIndex)
    }
    onTocSeek?.(timeMs)
  }, [onTocSeek, searchQuery, subtitles])

  return (
    <div className="flex h-full min-h-0">
      <div className="flex min-w-0 flex-1 flex-col">
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
      {tocTree && <TranscriptToc tree={tocTree} currentTime={currentTime} onSeek={handleTocSeek} />}
    </div>
  )
}
