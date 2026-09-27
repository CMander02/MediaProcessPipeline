import { useEffect, useRef, useState, type ReactNode } from "react"
import { HugeiconsIcon } from "@hugeicons/react"
import { ArrowRight01Icon } from "@hugeicons/core-free-icons"

import { formatDuration } from "@/lib/format"
import { chapterKey } from "@/lib/result-chapters"
import { cn } from "@/lib/utils"
import type { TranscriptTocNode } from "@/components/result/transcript-tab"

interface ChapterListProps {
  tree: TranscriptTocNode[]
  /** Chapter, section and sub-section playing now, outermost first */
  currentPath: TranscriptTocNode[]
  onSeek: (seconds: number) => void
  className?: string
}

/**
 * Chapters as a playlist, like Bilibili's 选集 list: numbered rows with their start time, the one
 * playing highlighted. A chapter opens to show its sections (and a section its sub-sections);
 * the playing chapter opens by itself.
 */
export function ChapterList({ tree, currentPath, onSeek, className }: ChapterListProps) {
  const currentKeys = new Set(currentPath.map(chapterKey))
  const [opened, setOpened] = useState<Set<string>>(() => new Set())
  const [closed, setClosed] = useState<Set<string>>(() => new Set())
  const listRef = useRef<HTMLDivElement>(null)
  const deepest = currentPath.at(-1)
  const deepestKey = deepest ? chapterKey(deepest) : null

  // Keep the playing row in view without yanking the list while it is being read.
  useEffect(() => {
    if (!deepestKey || !listRef.current || listRef.current.matches(":hover")) return
    const row = listRef.current.querySelector<HTMLElement>(`[data-chapter="${CSS.escape(deepestKey)}"]`)
    row?.scrollIntoView({ block: "nearest", behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" })
  }, [deepestKey])

  const isOpen = (key: string) => opened.has(key) || (currentKeys.has(key) && !closed.has(key))
  const toggle = (key: string) => {
    const open = isOpen(key)
    setOpened((current) => {
      const next = new Set(current)
      if (open) next.delete(key)
      else next.add(key)
      return next
    })
    setClosed((current) => {
      const next = new Set(current)
      if (open && currentKeys.has(key)) next.add(key)
      else next.delete(key)
      return next
    })
  }

  const renderRows = (nodes: TranscriptTocNode[], depth: number): ReactNode => nodes.map((node, index) => {
    const key = chapterKey(node)
    const current = currentKeys.has(key)
    const playing = key === deepestKey
    const hasChildren = Boolean(node.children?.length)
    const open = hasChildren && isOpen(key)
    const seekable = typeof node.start === "number"
    return (
      <div key={`${key}-${index}`}>
        <div
          data-chapter={key}
          className={cn(
            "group flex items-start gap-1 rounded-md pr-2 transition-colors",
            depth === 0 ? "py-1.5" : "py-1",
            current && depth === 0 ? "bg-primary/10" : "hover:bg-muted/70",
          )}
          style={{ paddingLeft: depth === 0 ? 4 : 4 + depth * 14 }}
        >
          {hasChildren ? (
            <button
              type="button"
              onClick={() => toggle(key)}
              aria-label={open ? `收起「${node.title}」` : `展开「${node.title}」`}
              aria-expanded={open}
              className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
            >
              <HugeiconsIcon icon={ArrowRight01Icon} className={cn("size-3.5 transition-transform duration-200", open && "rotate-90")} />
            </button>
          ) : (
            <span className="w-5 shrink-0" aria-hidden="true" />
          )}
          <button
            type="button"
            disabled={!seekable}
            onClick={() => seekable && onSeek(node.start!)}
            aria-current={playing ? "location" : undefined}
            className={cn(
              "flex min-w-0 flex-1 items-start gap-2 text-left",
              depth === 0 ? "text-[13px]" : "text-xs",
              current ? "text-primary" : depth === 0 ? "text-foreground" : "text-muted-foreground hover:text-foreground",
              playing && "font-medium",
            )}
            title={node.title}
          >
            {depth === 0 && (
              <span className={cn("w-6 shrink-0 pt-px text-xs tabular-nums", current ? "text-primary" : "text-muted-foreground")}>
                {String(index + 1).padStart(2, "0")}
              </span>
            )}
            {depth > 0 && playing && <span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-primary" aria-hidden="true" />}
            <span className={cn("min-w-0 flex-1 leading-5", depth > 0 && "line-clamp-2")}>{node.title}</span>
            {seekable && (
              <span className="shrink-0 pt-px text-xs tabular-nums text-muted-foreground">{formatDuration(node.start!)}</span>
            )}
          </button>
        </div>
        {open && <div>{renderRows(node.children!, depth + 1)}</div>}
      </div>
    )
  })

  return (
    <div ref={listRef} className={cn("flex flex-col gap-px", className)} role="list" aria-label="章节">
      {renderRows(tree, 0)}
    </div>
  )
}
