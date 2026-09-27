import { useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from "react"
import { HugeiconsIcon } from "@hugeicons/react"
import {
  Add01Icon,
  ComputerTerminal01Icon,
  Folder01Icon,
  Loading03Icon,
  Search01Icon,
  Settings01Icon,
  SubtitleIcon,
  UserIcon,
  Video01Icon,
} from "@hugeicons/core-free-icons"

import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog"
import { api, type TranscriptHit, type TranscriptSearchResult } from "@/lib/api"
import { openComposer } from "@/lib/composer-store"
import { formatDuration } from "@/lib/format"
import { libraryHash, navigate } from "@/lib/router"
import { closeSearch, openSearch, toggleSearch, useSearchOpen } from "@/lib/search-store"
import { matchParts, snippetAround } from "@/lib/search-snippet"
import { archiveDisplayTitle } from "@/lib/task-display"
import { cn } from "@/lib/utils"
import type { ArchiveItem } from "@/repositories/archive-types"

type IconData = Parameters<typeof HugeiconsIcon>[0]["icon"]

type Item =
  | { kind: "command"; key: string; label: string; icon: IconData; run: () => void }
  | { kind: "title"; key: string; archive: ArchiveItem }
  | { kind: "speaker"; key: string; path: string; title: string; speaker: string }
  | { kind: "hit"; key: string; path: string; title: string; hit: TranscriptHit; first: boolean; count: number }

interface Results {
  query: string
  titles: ArchiveItem[]
  transcripts: TranscriptSearchResult | null
}

const EMPTY: Results = { query: "", titles: [], transcripts: null }
// One character matches nearly every transcript; wait for a second one.
const MIN_TRANSCRIPT_QUERY = 2

const COMMANDS: Array<{ key: string; label: string; icon: IconData; run: () => void }> = [
  { key: "files", label: "文件库", icon: Folder01Icon, run: () => navigate(libraryHash()) },
  { key: "new", label: "新建处理", icon: Add01Icon, run: () => openComposer() },
  { key: "system", label: "后端 · 系统状态", icon: ComputerTerminal01Icon, run: () => navigate("#/backend") },
  { key: "logs", label: "后端 · 实时日志", icon: ComputerTerminal01Icon, run: () => navigate("#/backend?tab=logs") },
  { key: "settings", label: "设置", icon: Settings01Icon, run: () => navigate("#/settings") },
]

function archiveHash(path: string, seconds?: number): string {
  return `#/result/archive?path=${encodeURIComponent(path)}${seconds !== undefined ? `&t=${seconds}` : ""}`
}

function Highlight({ text, query }: { text: string; query: string }) {
  return (
    <>
      {matchParts(text, query).map((part, index) => (
        part.match
          ? <mark key={index} className="rounded-sm bg-amber-200/70 px-0.5 text-foreground dark:bg-amber-500/30">{part.text}</mark>
          : <span key={index}>{part.text}</span>
      ))}
    </>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div role="group" aria-label={title} className="py-1">
      <div className="px-3 pb-1 pt-2 text-xs font-medium text-muted-foreground">{title}</div>
      {children}
    </div>
  )
}

/** Ctrl+K: pages, archive titles, speakers and every line of every transcript. */
export function CommandSearch() {
  const open = useSearchOpen()
  const [query, setQuery] = useState("")
  const [results, setResults] = useState<Results>(EMPTY)
  const [activeIndex, setActiveIndex] = useState(0)
  const [pending, setPending] = useState(false)
  const listRef = useRef<HTMLDivElement>(null)
  const requestRef = useRef(0)
  const text = query.trim()

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey || event.key.toLowerCase() !== "k") return
      event.preventDefault()
      toggleSearch()
    }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  }, [])

  // Search as you type, a moment after the last key; a newer request wins.
  useEffect(() => {
    if (!open || !text) return
    const request = ++requestRef.current
    const timer = window.setTimeout(async () => {
      setPending(true)
      const [titles, transcripts] = await Promise.all([
        api.archives.page({ page: 1, page_size: 6, search: text, media: "all", source: "all", sort: "created_desc" })
          .then((page) => page.archives)
          .catch(() => [] as ArchiveItem[]),
        text.length >= MIN_TRANSCRIPT_QUERY
          ? api.searchTranscripts(text).catch(() => null)
          : Promise.resolve(null),
      ])
      if (request !== requestRef.current) return
      setResults({ query: text, titles, transcripts })
      setActiveIndex(0)
      setPending(false)
    }, 180)
    return () => window.clearTimeout(timer)
  }, [open, text])

  // While the index catches up with the library, ask again now and then.
  const indexing = results.query === text && results.transcripts?.indexing
  useEffect(() => {
    if (!open || !indexing) return
    const timer = window.setTimeout(async () => {
      const request = ++requestRef.current
      const transcripts = await api.searchTranscripts(text).catch(() => null)
      if (request === requestRef.current) setResults((current) => ({ ...current, transcripts }))
    }, 1500)
    return () => window.clearTimeout(timer)
  }, [indexing, open, text])

  const shown = results.query === text ? results : EMPTY
  const items = useMemo<Item[]>(() => {
    const lower = text.toLowerCase()
    const commands: Item[] = COMMANDS
      .filter((command) => !lower || command.label.toLowerCase().includes(lower))
      .map((command) => ({ kind: "command", ...command }))
    const titles: Item[] = shown.titles.map((archive) => ({ kind: "title", key: `title:${archive.path}`, archive }))
    const speakers: Item[] = (shown.transcripts?.speakers ?? []).slice(0, 5).map((entry) => ({
      kind: "speaker", key: `speaker:${entry.path}:${entry.speaker}`, ...entry,
    }))
    const hits: Item[] = (shown.transcripts?.groups ?? []).flatMap((group) => group.hits.map((hit, index) => ({
      kind: "hit" as const,
      key: `hit:${group.path}:${hit.start_ms}`,
      path: group.path,
      title: group.title,
      hit,
      first: index === 0,
      count: group.count,
    })))
    return [...commands, ...titles, ...speakers, ...hits]
  }, [shown, text])

  const active = Math.min(activeIndex, Math.max(0, items.length - 1))

  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" })
  }, [active])

  const choose = (item: Item) => {
    closeSearch()
    if (item.kind === "command") item.run()
    else if (item.kind === "title") navigate(archiveHash(item.archive.path))
    else if (item.kind === "speaker") navigate(archiveHash(item.path))
    else navigate(archiveHash(item.path, Math.floor(item.hit.start_ms / 1000)))
  }

  const onKeyDown = (event: ReactKeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault()
      if (!items.length) return
      const step = event.key === "ArrowDown" ? 1 : -1
      setActiveIndex((active + step + items.length) % items.length)
    } else if (event.key === "Enter" && items[active]) {
      event.preventDefault()
      choose(items[active])
    }
  }

  const row = (item: Item, index: number, content: ReactNode) => (
    <button
      key={item.key}
      type="button"
      role="option"
      aria-selected={index === active}
      data-index={index}
      onMouseMove={() => index !== active && setActiveIndex(index)}
      onClick={() => choose(item)}
      className={cn(
        "flex w-full min-w-0 items-center gap-3 px-3 py-2 text-left text-sm",
        index === active ? "bg-muted" : "",
      )}
    >
      {content}
    </button>
  )

  const commandItems = items.filter((item) => item.kind === "command")
  const titleItems = items.filter((item) => item.kind === "title")
  const speakerItems = items.filter((item) => item.kind === "speaker")
  const hitItems = items.filter((item) => item.kind === "hit")
  const indexOf = (item: Item) => items.indexOf(item)
  const progress = shown.transcripts?.progress
  const searched = shown.query === text && Boolean(text)
  const nothing = searched && !pending && !titleItems.length && !speakerItems.length && !hitItems.length

  return (
    <Dialog open={open} onOpenChange={(next) => (next ? openSearch() : closeSearch())}>
      <DialogContent
        showCloseButton={false}
        className="top-[10vh] flex max-h-[min(80vh,42rem)] translate-y-0 flex-col gap-0 overflow-hidden p-0 sm:max-w-2xl"
        onCloseAutoFocus={() => setQuery("")}
      >
        <DialogTitle className="sr-only">搜索</DialogTitle>
        <DialogDescription className="sr-only">搜索页面、标题、说话人和字幕内容</DialogDescription>
        <div className="flex items-center gap-2 border-b px-3">
          <HugeiconsIcon icon={Search01Icon} className="size-4 shrink-0 text-muted-foreground" />
          <input
            autoFocus
            value={query}
            onChange={(event) => { setQuery(event.target.value); setActiveIndex(0) }}
            onKeyDown={onKeyDown}
            placeholder="搜索标题、说话人和字幕里的话…"
            aria-label="搜索"
            role="combobox"
            aria-expanded="true"
            aria-controls="command-search-list"
            className="h-12 min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          />
          {pending && <HugeiconsIcon icon={Loading03Icon} className="size-4 shrink-0 animate-spin text-muted-foreground" />}
        </div>

        <div ref={listRef} id="command-search-list" role="listbox" className="min-h-0 flex-1 overflow-y-auto overscroll-contain py-1">
          {commandItems.length > 0 && (
            <Section title="前往">
              {commandItems.map((item) => item.kind === "command" && row(item, indexOf(item), (
                <>
                  <HugeiconsIcon icon={item.icon} className="size-4 shrink-0 text-muted-foreground" />
                  <span className="truncate">{item.label}</span>
                  {item.key === "new" && <kbd className="ml-auto text-xs text-muted-foreground">Ctrl N</kbd>}
                </>
              )))}
            </Section>
          )}

          {titleItems.length > 0 && (
            <Section title="标题">
              {titleItems.map((item) => item.kind === "title" && row(item, indexOf(item), (
                <>
                  <HugeiconsIcon icon={Video01Icon} className="size-4 shrink-0 text-muted-foreground" />
                  <span className="truncate"><Highlight text={archiveDisplayTitle(item.archive).title} query={text} /></span>
                  <span className="ml-auto shrink-0 text-xs text-muted-foreground">{item.archive.date}</span>
                </>
              )))}
            </Section>
          )}

          {speakerItems.length > 0 && (
            <Section title="说话人">
              {speakerItems.map((item) => item.kind === "speaker" && row(item, indexOf(item), (
                <>
                  <HugeiconsIcon icon={UserIcon} className="size-4 shrink-0 text-muted-foreground" />
                  <span className="shrink-0"><Highlight text={item.speaker} query={text} /></span>
                  <span className="min-w-0 truncate text-xs text-muted-foreground">{item.title}</span>
                </>
              )))}
            </Section>
          )}

          {hitItems.length > 0 && (
            <Section title="字幕">
              {hitItems.map((item) => item.kind === "hit" && (
                <div key={item.key}>
                  {item.first && (
                    <div className="flex min-w-0 items-center gap-2 px-3 pb-0.5 pt-2 text-xs">
                      <HugeiconsIcon icon={SubtitleIcon} className="size-3.5 shrink-0 text-muted-foreground" />
                      <span className="truncate font-medium">{item.title}</span>
                      <span className="shrink-0 text-muted-foreground">{item.count} 处</span>
                    </div>
                  )}
                  {row(item, indexOf(item), (
                    <>
                      <span className="w-[4.5rem] shrink-0 pl-4 text-right text-xs tabular-nums text-muted-foreground">
                        {formatDuration(item.hit.start_ms / 1000)}
                      </span>
                      <span className="min-w-0 flex-1 truncate">
                        {item.hit.speaker && <span className="mr-1.5 text-xs text-muted-foreground">{item.hit.speaker}</span>}
                        <Highlight text={snippetAround(item.hit.text, text)} query={text} />
                      </span>
                    </>
                  ))}
                </div>
              ))}
            </Section>
          )}

          {nothing && <p className="px-3 py-8 text-center text-sm text-muted-foreground">没有找到「{text}」</p>}
          {text.length > 0 && text.length < MIN_TRANSCRIPT_QUERY && (
            <p className="px-3 pb-2 pt-1 text-xs text-muted-foreground">再输入一个字，同时搜索字幕内容</p>
          )}
        </div>

        <div className="flex items-center gap-3 border-t px-3 py-2 text-xs text-muted-foreground">
          {indexing && progress ? (
            <span className="flex items-center gap-1.5">
              <HugeiconsIcon icon={Loading03Icon} className="size-3.5 animate-spin" />
              正在建立字幕索引 {progress[0]}/{progress[1]}，结果还不完整
            </span>
          ) : (
            <span>↑↓ 选择 · Enter 打开 · Esc 关闭</span>
          )}
          <span className="ml-auto hidden sm:inline">Ctrl K</span>
        </div>
      </DialogContent>
    </Dialog>
  )
}
