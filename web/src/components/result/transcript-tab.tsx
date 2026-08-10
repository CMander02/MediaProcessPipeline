import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import type { Subtitle } from "@/lib/srt"
import { subtitlesToSRT, extractSpeakers, findSubtitleIndexAtTime } from "@/lib/srt"
import { TranscriptSegment } from "./transcript-segment"
import { TranscriptSearch } from "./transcript-search"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { HugeiconsIcon } from "@hugeicons/react"
import {
  ArrowDown01Icon,
  ArrowUp01Icon,
  ListTreeIcon,
  PanelRightCloseIcon,
  PanelRightOpenIcon,
} from "@hugeicons/core-free-icons"
import { api } from "@/lib/api"
import { cn } from "@/lib/utils"

export interface TranscriptTocNode {
  title: string
  start?: number
  end?: number
  children?: TranscriptTocNode[]
}

interface TranscriptTabProps {
  subtitles: Subtitle[]
  currentSegmentIndex: number
  autoScroll: boolean
  currentTime?: number
  onSegmentClick: (subtitle: Subtitle) => void
  onManualScroll: () => void
  onTocSeek?: (timeMs: number) => void
  tocNodes?: TranscriptTocNode[] | null
  /** Path to the SRT file for saving edits */
  srtPath?: string
  /** Called when subtitles are modified */
  onSubtitlesChange?: (subtitles: Subtitle[]) => void
}

function formatChapterTime(seconds: number): string {
  const totalSeconds = Math.max(0, Math.floor(seconds))
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const remaining = totalSeconds % 60
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(remaining).padStart(2, "0")}`
    : `${minutes}:${String(remaining).padStart(2, "0")}`
}

function filterTocNodes(nodes: TranscriptTocNode[], query: string): TranscriptTocNode[] {
  const normalized = query.trim().toLocaleLowerCase()
  if (!normalized) return nodes
  return nodes.flatMap((node) => {
    const children = filterTocNodes(node.children ?? [], query)
    if (node.title.toLocaleLowerCase().includes(normalized)) return [node]
    if (children.length > 0) return [{ ...node, children }]
    return []
  })
}

function flattenSeekableNodes(nodes: TranscriptTocNode[]): TranscriptTocNode[] {
  const flattened: TranscriptTocNode[] = []
  const walk = (items: TranscriptTocNode[]) => {
    for (const node of items) {
      if (typeof node.start === "number") flattened.push(node)
      if (node.children?.length) walk(node.children)
    }
  }
  walk(nodes)
  return flattened
}

function findCurrentChapterIndex(nodes: TranscriptTocNode[], currentTime: number): number {
  let current = -1
  for (let index = 0; index < nodes.length; index += 1) {
    const start = nodes[index].start
    if (typeof start !== "number" || start > currentTime) break
    current = index
    const end = nodes[index].end
    if (typeof end === "number" && currentTime < end) break
  }
  return current
}

function chapterKey(node: TranscriptTocNode): string {
  return `${node.start ?? "group"}:${node.end ?? "open"}:${node.title}`
}

function findActiveChapterKey(nodes: TranscriptTocNode[], currentTime: number): string | null {
  let match: string | null = null
  nodes.forEach((node) => {
    const key = chapterKey(node)
    if (typeof node.start === "number") {
      const end = typeof node.end === "number" ? node.end : Number.POSITIVE_INFINITY
      if (currentTime >= node.start && currentTime < end) match = key
    }
    const childMatch = findActiveChapterKey(node.children ?? [], currentTime)
    if (childMatch) match = childMatch
  })
  return match
}

function ChapterNode({
  node,
  depth,
  activeKey,
  mobile,
  onSeek,
}: {
  node: TranscriptTocNode
  depth: number
  activeKey: string | null
  mobile?: boolean
  onSeek?: (seconds: number) => void
}) {
  const key = chapterKey(node)
  const active = key === activeKey
  const seekable = typeof node.start === "number"
  return (
    <li data-chapter-key={key}>
      <Button
        variant="ghost"
        size="sm"
        disabled={!seekable}
        onClick={() => seekable && onSeek?.(node.start!)}
        className={cn(
          "relative h-auto w-full justify-start whitespace-normal rounded-md py-2 pr-2 text-left text-xs leading-5",
          mobile ? "min-h-11" : "min-h-8",
          seekable ? "hover:bg-muted" : "cursor-default text-muted-foreground",
          active && "bg-primary/10 font-medium text-primary hover:bg-primary/10",
        )}
        style={{ paddingLeft: `${12 + depth * 14}px` }}
        title={seekable ? `${node.title} · ${formatChapterTime(node.start!)}` : node.title}
        aria-current={active ? "location" : undefined}
      >
        {active ? <span aria-hidden="true" className="absolute inset-y-2 left-1 w-0.5 rounded-full bg-primary" /> : null}
        {node.title}
      </Button>
      {node.children?.length ? (
        <ul className="mt-0.5 flex flex-col gap-0.5">
          {node.children.map((child, index) => (
            <ChapterNode
              key={`${child.title}-${index}`}
              node={child}
              depth={depth + 1}
              activeKey={activeKey}
              mobile={mobile}
              onSeek={onSeek}
            />
          ))}
        </ul>
      ) : null}
    </li>
  )
}

function ChapterTocContent({
  nodes,
  currentTime,
  onSeek,
  mobile,
}: {
  nodes: TranscriptTocNode[]
  currentTime: number
  onSeek?: (seconds: number) => void
  mobile?: boolean
}) {
  const [query, setQuery] = useState("")
  const listRef = useRef<HTMLUListElement>(null)
  const visibleNodes = useMemo(() => filterTocNodes(nodes, query), [nodes, query])
  const seekableNodes = useMemo(() => flattenSeekableNodes(nodes), [nodes])
  const currentIndex = findCurrentChapterIndex(seekableNodes, currentTime)
  const activeKey = findActiveChapterKey(nodes, currentTime)

  useEffect(() => {
    if (!activeKey || query) return
    const active = Array.from(listRef.current?.querySelectorAll<HTMLElement>("[data-chapter-key]") ?? [])
      .find((element) => element.dataset.chapterKey === activeKey)
    if (!active) return
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches
    active.scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth", block: "nearest" })
  }, [activeKey, query])

  const moveChapter = (offset: number) => {
    const targetIndex = Math.min(Math.max(currentIndex + offset, 0), seekableNodes.length - 1)
    const start = seekableNodes[targetIndex]?.start
    if (typeof start === "number") onSeek?.(start)
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center gap-1.5 border-b p-2">
        <label htmlFor={mobile ? "mobile-chapter-search" : "desktop-chapter-search"} className="sr-only">筛选章节</label>
        <Input
          id={mobile ? "mobile-chapter-search" : "desktop-chapter-search"}
          name="chapter-search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="筛选章节…"
          autoComplete="off"
          className={cn("min-w-0 flex-1", mobile && "h-11")}
        />
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size={mobile ? "icon-lg" : "icon-sm"}
                type="button"
                onClick={() => moveChapter(-1)}
                disabled={currentIndex <= 0}
                aria-label="上一章节"
              >
                <HugeiconsIcon icon={ArrowUp01Icon} />
              </Button>
            </TooltipTrigger>
            <TooltipContent>上一章节</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size={mobile ? "icon-lg" : "icon-sm"}
                type="button"
                onClick={() => moveChapter(1)}
                disabled={currentIndex >= seekableNodes.length - 1}
                aria-label="下一章节"
              >
                <HugeiconsIcon icon={ArrowDown01Icon} />
              </Button>
            </TooltipTrigger>
            <TooltipContent>下一章节</TooltipContent>
          </Tooltip>
        </TooltipProvider>
      </div>
      <ScrollArea className="min-h-0 flex-1 overscroll-contain">
        {visibleNodes.length > 0 ? (
          <ul ref={listRef} className="flex flex-col gap-0.5 p-2">
            {visibleNodes.map((node, index) => (
              <ChapterNode
                key={`${node.title}-${index}`}
                node={node}
                depth={0}
                activeKey={activeKey}
                mobile={mobile}
                onSeek={onSeek}
              />
            ))}
          </ul>
        ) : (
          <p className="px-4 py-8 text-center text-sm text-muted-foreground">没有匹配章节</p>
        )}
      </ScrollArea>
      <p className="shrink-0 border-t px-3 py-2 text-xs text-muted-foreground" aria-live="polite">
        {query ? `${visibleNodes.length} 个匹配章节` : `${seekableNodes.length} 个章节`}
      </p>
    </div>
  )
}

function DesktopChapterToc({ nodes, currentTime, onSeek }: {
  nodes: TranscriptTocNode[]
  currentTime: number
  onSeek?: (seconds: number) => void
}) {
  const [collapsed, setCollapsed] = useState(false)
  return (
    <aside className={cn(
      "hidden shrink-0 border-l bg-muted/10 transition-[width] duration-150 motion-reduce:transition-none xl:flex xl:flex-col",
      collapsed ? "w-12" : "w-72",
    )} aria-label="字幕章节目录">
      <div className={cn("flex h-11 shrink-0 items-center border-b", collapsed ? "justify-center" : "justify-between px-3")}>
        {collapsed ? null : (
          <div className="flex min-w-0 items-center gap-2">
            <HugeiconsIcon icon={ListTreeIcon} aria-hidden="true" />
            <span className="truncate text-sm font-medium">字幕目录</span>
          </div>
        )}
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          onClick={() => setCollapsed((value) => !value)}
          aria-expanded={!collapsed}
          aria-label={collapsed ? "展开字幕目录" : "收起字幕目录"}
          title={collapsed ? "展开字幕目录" : "收起字幕目录"}
        >
          <HugeiconsIcon icon={collapsed ? PanelRightOpenIcon : PanelRightCloseIcon} />
        </Button>
      </div>
      {collapsed ? null : <ChapterTocContent nodes={nodes} currentTime={currentTime} onSeek={onSeek} />}
    </aside>
  )
}

function MobileChapterToc({ nodes, currentTime, onSeek }: {
  nodes: TranscriptTocNode[]
  currentTime: number
  onSeek?: (seconds: number) => void
}) {
  const [open, setOpen] = useState(false)
  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button type="button" variant="outline" size="icon-lg" className="xl:hidden" aria-label="打开字幕目录">
          <HugeiconsIcon icon={ListTreeIcon} />
        </Button>
      </SheetTrigger>
      <SheetContent className="w-[min(90vw,22rem)] gap-0 overscroll-contain pb-[env(safe-area-inset-bottom)]" side="right">
        <SheetHeader className="border-b pr-14">
          <SheetTitle>字幕目录</SheetTitle>
          <SheetDescription>选择章节并跳转到对应字幕</SheetDescription>
        </SheetHeader>
        <ChapterTocContent
          nodes={nodes}
          currentTime={currentTime}
          mobile
          onSeek={(seconds) => {
            onSeek?.(seconds)
            setOpen(false)
          }}
        />
      </SheetContent>
    </Sheet>
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
  tocNodes,
  srtPath,
  onSubtitlesChange,
}: TranscriptTabProps) {
  const [searchQuery, setSearchQuery] = useState("")
  const [editingIndex, setEditingIndex] = useState<number | null>(null)
  const [isNewInsert, setIsNewInsert] = useState(false) // track if editing a freshly inserted subtitle
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
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches
    el.scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth", block: "center" })
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
      window.setTimeout(() => scrollToSegment(targetIndex), 0)
    }
    onTocSeek?.(timeMs)
  }, [onTocSeek, scrollToSegment, searchQuery, subtitles])

  return (
    <div className="flex h-full min-h-0">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-2 border-b px-3 py-2">
          <div className="min-w-0 flex-1">
            <TranscriptSearch
              value={searchQuery}
              onChange={setSearchQuery}
              matchCount={matchCount}
            />
          </div>
          {tocNodes?.length ? <MobileChapterToc nodes={tocNodes} currentTime={currentTime} onSeek={handleTocSeek} /> : null}
        </div>
        <ScrollArea className="flex-1 min-h-0" onScrollCapture={handleScroll}>
          <div ref={scrollRef} className="flex flex-col gap-0.5 py-2">
            {filteredIndices.length === 0 ? (
              <p className="text-sm text-muted-foreground text-center py-8">
                {searchQuery ? "无匹配结果" : "无字幕数据"}
              </p>
            ) : (
              filteredIndices.map((idx) => (
                <div
                  key={`${idx}-${subtitles[idx]?.startTime}`}
                  ref={(el) => setSegmentRef(idx, el)}
                  className="[content-visibility:auto] [contain-intrinsic-size:auto_72px]"
                >
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
      {tocNodes?.length ? <DesktopChapterToc nodes={tocNodes} currentTime={currentTime} onSeek={handleTocSeek} /> : null}
    </div>
  )
}
