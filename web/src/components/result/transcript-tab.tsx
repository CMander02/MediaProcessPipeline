import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react"
import type { Subtitle } from "@/lib/srt"
import { subtitlesToSRT, extractSpeakers } from "@/lib/srt"
import { TranscriptSegment } from "./transcript-segment"
import { ChapterList } from "./chapter-list"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { HugeiconsIcon } from "@hugeicons/react"
import {
  ArrowDown01Icon,
  ArrowUp01Icon,
  Cancel01Icon,
  LanguageSkillIcon,
  ListTreeIcon,
  Search01Icon,
} from "@hugeicons/core-free-icons"
import { api } from "@/lib/api"
import { formatDuration } from "@/lib/format"
import { chapterKey, chapterPathAt, flattenChapterTree } from "@/lib/result-chapters"
import { DEFAULT_TRACK, languageName, trackKind, type SubtitleTrackInfo } from "@/lib/subtitle-tracks"
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
  /** Chapters with sections and sub-sections */
  tocNodes?: TranscriptTocNode[] | null
  /** Path to the SRT file for saving edits */
  srtPath?: string
  /** Called when subtitles are modified */
  onSubtitlesChange?: (subtitles: Subtitle[]) => void
  onEditingChange?: (editing: boolean) => void
  onSaved?: (srt: string) => void
  editingDisabled?: boolean
  /** Other-language tracks are shown read-only */
  readOnly?: boolean
  tracks?: SubtitleTrackInfo[]
  shownTrackLang?: string | null
  onSelectTrack?: (lang: string) => void
  secondaryTrackLang?: string | null
  onSelectSecondary?: (lang: string | null) => void
  /** Second-language text under each line, aligned by time */
  secondaryTexts?: string[] | null
}

const TOC_KEY = "mpp-transcript-toc"
// Below this width the chapter list opens as a sheet instead of a side card.
const SIDE_PANEL_MIN_WIDTH = 720

function readTocOpen(): boolean {
  try {
    return localStorage.getItem(TOC_KEY) !== "0"
  } catch {
    return true
  }
}

/** First line starting at or after `timeMs` (lines sorted by start). */
function firstLineFrom(subtitles: Subtitle[], timeMs: number): number {
  let low = 0
  let high = subtitles.length
  while (low < high) {
    const mid = (low + high) >> 1
    if (subtitles[mid].startTime < timeMs) low = mid + 1
    else high = mid
  }
  return low
}

function LanguageMenu({ tracks, shown, secondary, onSelect, onSelectSecondary, readOnly }: {
  tracks: SubtitleTrackInfo[]
  shown: string | null
  secondary: string | null
  onSelect?: (lang: string) => void
  onSelectSecondary?: (lang: string | null) => void
  readOnly?: boolean
}) {
  const label = (lang: string | null) => (lang ? languageName(lang) : "默认字幕")
  // The polished default is not always one of the listed tracks (older archives).
  const hasPolishedTrack = tracks.some((track) => track.polished)
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-8 shrink-0 gap-1.5 px-2 text-xs font-normal text-muted-foreground"
          title={readOnly ? "这一种语言没有经过润色，只能查看" : "字幕语言与对照"}
        >
          <HugeiconsIcon icon={LanguageSkillIcon} className="size-4" />
          {label(shown)}
          {secondary && <span className="text-foreground/70">+ {languageName(secondary)}</span>}
          {readOnly && <span className="rounded bg-muted px-1 text-[11px]">只读</span>}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-64">
        <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">显示</DropdownMenuLabel>
        <DropdownMenuRadioGroup value={shown ?? DEFAULT_TRACK} onValueChange={(value) => onSelect?.(value)}>
          {!hasPolishedTrack && (
            <DropdownMenuRadioItem value={DEFAULT_TRACK}>
              <span className="flex-1">默认字幕</span>
              <span className="pl-3 text-xs text-muted-foreground">润色 · 带说话人</span>
            </DropdownMenuRadioItem>
          )}
          {tracks.map((track) => (
            <DropdownMenuRadioItem key={track.lang} value={track.lang}>
              <span className="flex-1">{languageName(track.lang)}</span>
              <span className="pl-3 text-xs text-muted-foreground">{trackKind(track)}</span>
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
        <DropdownMenuSeparator />
        <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">对照（显示在每句下方）</DropdownMenuLabel>
        <DropdownMenuRadioGroup value={secondary ?? ""} onValueChange={(value) => onSelectSecondary?.(value || null)}>
          <DropdownMenuRadioItem value="">不对照</DropdownMenuRadioItem>
          {tracks.filter((track) => track.lang !== shown).map((track) => (
            <DropdownMenuRadioItem key={track.lang} value={track.lang}>
              <span className="flex-1">{languageName(track.lang)}</span>
              <span className="pl-3 text-xs text-muted-foreground">{trackKind(track)}</span>
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
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
  onEditingChange,
  onSaved,
  editingDisabled = false,
  readOnly = false,
  tracks = [],
  shownTrackLang = null,
  onSelectTrack,
  secondaryTrackLang = null,
  onSelectSecondary,
  secondaryTexts = null,
}: TranscriptTabProps) {
  const tree = useMemo(() => tocNodes ?? [], [tocNodes])
  const [searchOpen, setSearchOpen] = useState(false)
  const [searchQuery, setSearchQuery] = useState("")
  const [matchCursor, setMatchCursor] = useState(0)
  const [editingIndex, setEditingIndex] = useState<number | null>(null)
  const [isNewInsert, setIsNewInsert] = useState(false) // track if editing a freshly inserted subtitle
  const [savingCount, setSavingCount] = useState(0)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [tocOpen, setTocOpen] = useState(readTocOpen)
  const [sheetOpen, setSheetOpen] = useState(false)
  const [wide, setWide] = useState(true)
  const [readingIndex, setReadingIndex] = useState(0)
  const containerRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const searchInputRef = useRef<HTMLInputElement>(null)
  const segmentRefs = useRef<Map<number, HTMLDivElement>>(new Map())
  const isUserScrolling = useRef(false)
  const programmaticScroll = useRef(false)
  const readingFrame = useRef(0)

  useEffect(() => {
    onEditingChange?.(editingIndex !== null || savingCount > 0)
    return () => onEditingChange?.(false)
  }, [editingIndex, savingCount, onEditingChange])

  // Side card when there is room, a sheet otherwise; decided by this tab's own width.
  useEffect(() => {
    const element = containerRef.current
    if (!element || typeof ResizeObserver === "undefined") return
    const observer = new ResizeObserver(([entry]) => setWide(entry.contentRect.width >= SIDE_PANEL_MIN_WIDTH))
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  const query = searchOpen ? searchQuery.trim().toLowerCase() : ""
  const matches = useMemo(() => {
    if (!query) return []
    return subtitles.flatMap((line, index) => (
      line.text.toLowerCase().includes(query) || secondaryTexts?.[index]?.toLowerCase().includes(query) ? [index] : []
    ))
  }, [query, secondaryTexts, subtitles])
  const currentMatch = matches.length ? matches[Math.min(matchCursor, matches.length - 1)] : -1

  const scrollToSegment = useCallback((index: number) => {
    const el = segmentRefs.current.get(index)
    if (!el) return false
    programmaticScroll.current = true
    const viewport = el.closest<HTMLElement>("[data-radix-scroll-area-viewport]")
    const offCenter = () => {
      if (!viewport) return 0
      const box = viewport.getBoundingClientRect()
      const line = el.getBoundingClientRect()
      return line.top + line.height / 2 - (box.top + box.height / 2)
    }
    const far = viewport ? Math.abs(offCenter()) > viewport.clientHeight * 2 : false
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches
    el.scrollIntoView({ behavior: reducedMotion || far ? "auto" : "smooth", block: "center" })
    window.setTimeout(() => {
      // Lines off screen are laid out at an estimated height, so a long jump can land short.
      if (Math.abs(offCenter()) > 24) el.scrollIntoView({ behavior: "auto", block: "center" })
      window.setTimeout(() => {
        programmaticScroll.current = false
      }, 50)
    }, far ? 60 : 400)
    return true
  }, [])

  // Follow playback, except while finding, editing or reading elsewhere.
  useEffect(() => {
    if (!autoScroll || currentSegmentIndex < 0 || isUserScrolling.current) return
    if (query || editingIndex !== null) return
    scrollToSegment(currentSegmentIndex)
  }, [currentSegmentIndex, autoScroll, query, editingIndex, scrollToSegment])

  useEffect(() => {
    if (currentMatch >= 0) scrollToSegment(currentMatch)
  }, [currentMatch, scrollToSegment])

  // The line at the top of the view decides which section the breadcrumb shows.
  const updateReading = useCallback(() => {
    cancelAnimationFrame(readingFrame.current)
    readingFrame.current = requestAnimationFrame(() => {
      const viewport = scrollRef.current?.closest<HTMLElement>("[data-radix-scroll-area-viewport]")
      if (!viewport || typeof document.elementFromPoint !== "function") return
      const box = viewport.getBoundingClientRect()
      const hit = document.elementFromPoint(box.left + 32, box.top + 10)?.closest<HTMLElement>("[data-seg-index]")
      if (hit) setReadingIndex(Number(hit.dataset.segIndex) || 0)
    })
  }, [])

  useEffect(() => {
    updateReading()
    return () => cancelAnimationFrame(readingFrame.current)
  }, [subtitles, updateReading])

  const handleScroll = useCallback(() => {
    updateReading()
    if (programmaticScroll.current) return
    isUserScrolling.current = true
    onManualScroll()
    setTimeout(() => {
      isUserScrolling.current = false
    }, 200)
  }, [onManualScroll, updateReading])

  const setSegmentRef = useCallback((index: number, el: HTMLDivElement | null) => {
    if (el) segmentRefs.current.set(index, el)
    else segmentRefs.current.delete(index)
  }, [])

  // Ctrl+F finds in the transcript while this tab is showing.
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || event.shiftKey || event.altKey || event.key.toLowerCase() !== "f") return
      if (!containerRef.current || containerRef.current.offsetParent === null) return
      event.preventDefault()
      setSearchOpen(true)
      requestAnimationFrame(() => searchInputRef.current?.select())
    }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  }, [])

  const closeSearch = () => {
    setSearchOpen(false)
    setSearchQuery("")
    setMatchCursor(0)
  }

  const stepMatch = (offset: number) => {
    if (!matches.length) return
    setMatchCursor((cursor) => (Math.min(cursor, matches.length - 1) + offset + matches.length) % matches.length)
  }

  const handleSearchKey = (event: ReactKeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") {
      event.preventDefault()
      stepMatch(event.shiftKey ? -1 : 1)
    } else if (event.key === "Escape") {
      event.preventDefault()
      closeSearch()
    }
  }

  // Save changes to file
  const saveSubtitles = useCallback(async (updated: Subtitle[]) => {
    onSubtitlesChange?.(updated)
    if (srtPath) {
      const srt = subtitlesToSRT(updated)
      setSavingCount((count) => count + 1)
      setSaveError(null)
      try {
        const result = await api.filesystem.write(srtPath, srt)
        if (!result.success) throw new Error(result.error || "文件保存失败")
        onSaved?.(srt)
      } catch (err) {
        console.warn("Failed to save SRT:", err)
        setSaveError("字幕尚未保存，请重新编辑并保存。")
      } finally {
        setSavingCount((count) => count - 1)
      }
    }
  }, [srtPath, onSubtitlesChange, onSaved])

  const handleEdit = useCallback((index: number, changes: Partial<Subtitle>) => {
    const updated = subtitles.map((sub, i) => (i === index ? { ...sub, ...changes } : sub))
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
    const newSub: Subtitle = { index: targetIdx + 1, startTime, endTime, text: "", speaker: subtitles[index]?.speaker }
    const updated = [...subtitles.slice(0, targetIdx), newSub, ...subtitles.slice(targetIdx)]
    onSubtitlesChange?.(updated)
    setEditingIndex(targetIdx)
    setIsNewInsert(true)
  }, [subtitles, onSubtitlesChange])

  const handleEditCancel = useCallback((index: number) => {
    if (isNewInsert && editingIndex === index) {
      onSubtitlesChange?.(subtitles.filter((_, i) => i !== index))
    }
    setEditingIndex(null)
    setIsNewInsert(false)
  }, [isNewInsert, editingIndex, subtitles, onSubtitlesChange])

  const speakers = useMemo(() => extractSpeakers(subtitles), [subtitles])

  const handleTocSeek = useCallback((seconds: number) => {
    const timeMs = Math.max(0, Math.round(seconds * 1000))
    const targetIndex = Math.min(firstLineFrom(subtitles, timeMs), subtitles.length - 1)
    if (targetIndex >= 0) window.setTimeout(() => scrollToSegment(targetIndex), 0)
    onTocSeek?.(timeMs)
  }, [onTocSeek, scrollToSegment, subtitles])

  // Section headings inside the transcript, placed before the first line of each section.
  const headings = useMemo(() => {
    const byLine = new Map<number, Array<{ node: TranscriptTocNode; depth: number; number: number }>>()
    let chapterNumber = 0
    for (const { node, depth } of flattenChapterTree(tree)) {
      if (depth === 0) chapterNumber += 1
      if (depth > 1 || typeof node.start !== "number") continue
      const line = firstLineFrom(subtitles, node.start * 1000 - 300)
      if (line >= subtitles.length) continue
      const list = byLine.get(line) ?? []
      list.push({ node, depth, number: chapterNumber })
      byLine.set(line, list)
    }
    return byLine
  }, [subtitles, tree])

  const readingSeconds = (subtitles[readingIndex]?.startTime ?? currentTime * 1000) / 1000
  const readingPath = useMemo(() => chapterPathAt(tree, readingSeconds), [tree, readingSeconds])
  const playingPath = useMemo(() => chapterPathAt(tree, currentTime), [tree, currentTime])
  const chapterNumber = readingPath[0] ? tree.findIndex((node) => chapterKey(node) === chapterKey(readingPath[0])) + 1 : 0
  const panelVisible = wide && tocOpen && tree.length > 0

  const toggleToc = () => {
    if (!wide) {
      setSheetOpen(true)
      return
    }
    setTocOpen((open) => {
      try {
        localStorage.setItem(TOC_KEY, open ? "0" : "1")
      } catch {
        // Remembering the choice is a convenience only.
      }
      return !open
    })
  }

  const chapterPanel = (
    <ChapterList tree={tree} currentPath={playingPath} onSeek={handleTocSeek} />
  )

  return (
    <div ref={containerRef} className="flex h-full min-h-0 flex-col">
      {saveError && <p role="alert" className="px-3 pt-2 text-xs text-destructive">{saveError}</p>}

      {/* Toolbar: where you are, language, find, chapters */}
      <div className="flex h-10 shrink-0 items-center gap-1 px-2">
        {searchOpen ? (
          <div className="flex h-8 min-w-0 flex-1 items-center gap-1 rounded-md border bg-background pl-2 pr-1 focus-within:ring-2 focus-within:ring-ring/40">
            <HugeiconsIcon icon={Search01Icon} className="size-3.5 shrink-0 text-muted-foreground" />
            <input
              ref={searchInputRef}
              autoFocus
              value={searchQuery}
              onChange={(event) => { setSearchQuery(event.target.value); setMatchCursor(0) }}
              onKeyDown={handleSearchKey}
              placeholder="搜索字幕…（Enter 下一个）"
              aria-label="搜索字幕"
              className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
            />
            <span className="shrink-0 text-xs tabular-nums text-muted-foreground" aria-live="polite">
              {query ? (matches.length ? `${Math.min(matchCursor, matches.length - 1) + 1}/${matches.length}` : "无结果") : ""}
            </span>
            <Button type="button" variant="ghost" size="icon-sm" className="size-6" onClick={() => stepMatch(-1)} disabled={!matches.length} aria-label="上一个">
              <HugeiconsIcon icon={ArrowUp01Icon} className="size-3.5" />
            </Button>
            <Button type="button" variant="ghost" size="icon-sm" className="size-6" onClick={() => stepMatch(1)} disabled={!matches.length} aria-label="下一个">
              <HugeiconsIcon icon={ArrowDown01Icon} className="size-3.5" />
            </Button>
            <Button type="button" variant="ghost" size="icon-sm" className="size-6" onClick={closeSearch} aria-label="关闭搜索">
              <HugeiconsIcon icon={Cancel01Icon} className="size-3.5" />
            </Button>
          </div>
        ) : (
          <nav className="flex min-w-0 flex-1 items-center gap-1 pl-1 text-xs text-muted-foreground" aria-label="当前位置">
            {readingPath.length ? readingPath.map((node, index) => (
              <span
                key={chapterKey(node)}
                className={cn("flex min-w-0 items-center gap-1", index === 0 ? "max-w-[45%] shrink-0" : "flex-1")}
              >
                {index > 0 && <span aria-hidden="true">›</span>}
                <button
                  type="button"
                  onClick={() => typeof node.start === "number" && handleTocSeek(node.start)}
                  className={cn(
                    "min-w-0 truncate rounded px-1 py-0.5 text-left hover:bg-muted hover:text-foreground",
                    index === 0 && "font-medium text-foreground",
                  )}
                  title={node.title}
                >
                  {index === 0 && chapterNumber > 0 && <span className="mr-1 tabular-nums text-muted-foreground">{String(chapterNumber).padStart(2, "0")}</span>}
                  {node.title}
                </button>
              </span>
            )) : (
              <span className="truncate pl-1">{subtitles.length} 句</span>
            )}
          </nav>
        )}

        {tracks.length > 1 && (
          <LanguageMenu
            tracks={tracks}
            shown={shownTrackLang}
            secondary={secondaryTrackLang}
            onSelect={onSelectTrack}
            onSelectSecondary={onSelectSecondary}
            readOnly={readOnly}
          />
        )}
        {!searchOpen && (
          <Button type="button" variant="ghost" size="icon-sm" className="size-8 shrink-0 text-muted-foreground" onClick={() => setSearchOpen(true)} aria-label="搜索字幕" title="搜索字幕 (Ctrl+F)">
            <HugeiconsIcon icon={Search01Icon} className="size-4" />
          </Button>
        )}
        {tree.length > 0 && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className={cn("h-8 shrink-0 gap-1.5 px-2 text-xs font-normal", panelVisible ? "bg-muted text-foreground" : "text-muted-foreground")}
            onClick={toggleToc}
            aria-pressed={panelVisible}
            title={panelVisible ? "收起目录" : "显示目录"}
          >
            <HugeiconsIcon icon={ListTreeIcon} className="size-4" />
            目录
          </Button>
        )}
      </div>

      {/* Transcript and chapter card; the card slides in and out */}
      <div
        className={cn(
          "grid min-h-0 flex-1 transition-[grid-template-columns] duration-300 ease-out motion-reduce:transition-none",
          panelVisible ? "grid-cols-[minmax(0,1fr)_18rem]" : "grid-cols-[minmax(0,1fr)_0rem]",
        )}
      >
        <ScrollArea className="min-h-0" onScrollCapture={handleScroll}>
          <div ref={scrollRef} inert={editingDisabled} className="flex flex-col gap-0.5 pb-3">
            {subtitles.length === 0 ? (
              <p className="py-8 text-center text-sm text-muted-foreground">无字幕数据</p>
            ) : (
              subtitles.map((subtitle, idx) => (
                <div
                  key={`${idx}-${subtitle.startTime}`}
                  ref={(el) => setSegmentRef(idx, el)}
                  data-seg-index={idx}
                  className="[content-visibility:auto] [contain-intrinsic-size:auto_72px]"
                >
                  {headings.get(idx)?.map(({ node, depth, number }) => (
                    <button
                      key={chapterKey(node)}
                      type="button"
                      onClick={() => handleTocSeek(node.start!)}
                      className={cn(
                        "flex w-full items-baseline gap-2 px-3 text-left transition-colors hover:text-primary",
                        depth === 0 ? "pt-5 pb-1" : "pt-2 pb-0.5 pl-6",
                      )}
                    >
                      {depth === 0 && <span className="text-xs tabular-nums text-muted-foreground">{String(number).padStart(2, "0")}</span>}
                      <span className={depth === 0 ? "text-sm font-semibold" : "text-xs font-medium text-muted-foreground"}>{node.title}</span>
                      <span className="ml-auto shrink-0 text-xs tabular-nums text-muted-foreground">{formatDuration(node.start!)}</span>
                    </button>
                  ))}
                  <TranscriptSegment
                    subtitle={subtitle}
                    isActive={idx === currentSegmentIndex}
                    isCurrentMatch={idx === currentMatch}
                    searchQuery={query}
                    secondaryText={secondaryTexts?.[idx]}
                    readOnly={readOnly}
                    editing={editingIndex === idx}
                    speakers={speakers}
                    onClick={() => onSegmentClick(subtitle)}
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

        <aside className="min-h-0 overflow-hidden" aria-label="章节目录" aria-hidden={!panelVisible} inert={!panelVisible}>
          <div className={cn("flex h-full w-[18rem] flex-col pb-2 pl-2 pr-1 transition-opacity duration-300", panelVisible ? "opacity-100" : "opacity-0")}>
            <div className="flex min-h-0 flex-1 flex-col rounded-lg bg-muted/40">
              <div className="flex h-9 shrink-0 items-center justify-between px-3">
                <span className="text-xs font-medium">目录 <span className="font-normal text-muted-foreground">· {tree.length} 章</span></span>
              </div>
              <ScrollArea className="min-h-0 flex-1 overscroll-contain">
                <div className="px-1.5 pb-2">{chapterPanel}</div>
              </ScrollArea>
            </div>
          </div>
        </aside>
      </div>

      {!wide && tree.length > 0 && (
        <Sheet open={sheetOpen} onOpenChange={setSheetOpen}>
          <SheetContent className="w-[min(90vw,22rem)] gap-0 overscroll-contain pb-[env(safe-area-inset-bottom)]" side="right">
            <SheetHeader className="border-b pr-14">
              <SheetTitle>目录</SheetTitle>
              <SheetDescription>{tree.length} 章，点标题跳到对应位置</SheetDescription>
            </SheetHeader>
            <ScrollArea className="min-h-0 flex-1">
              <div className="p-2">
                <ChapterList
                  tree={tree}
                  currentPath={playingPath}
                  onSeek={(seconds) => {
                    handleTocSeek(seconds)
                    setSheetOpen(false)
                  }}
                />
              </div>
            </ScrollArea>
          </SheetContent>
        </Sheet>
      )}
    </div>
  )
}
