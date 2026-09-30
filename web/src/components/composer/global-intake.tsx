import { useEffect, useRef, useState } from "react"
import { toast } from "sonner"
import { HugeiconsIcon } from "@hugeicons/react"
import { Upload01Icon } from "@hugeicons/core-free-icons"

import { api } from "@/lib/api"
import { isComposerShowing, openComposer, useComposerState } from "@/lib/composer-store"
import { isMediaFile, parseSources } from "@/lib/source-input"
import { sourceLabel } from "@/lib/task-display"
import { useAppAccess } from "@/hooks/use-app-access-context"
import { getPreferences } from "@/hooks/use-preferences"
import { libraryStateOf } from "@/components/composer/source-meta"

const SEEN_KEY = "mpp-clipboard-seen"
/** How many offered links are remembered; older ones may be offered again. */
const SEEN_LIMIT = 200

function isEditable(target: EventTarget | null): boolean {
  const element = target as HTMLElement | null
  return Boolean(element?.closest?.("input, textarea, select, [contenteditable=''], [contenteditable='true']"))
}

/** Another dialog (rename, delete, settings…) owns the keyboard and clipboard while open. */
function otherDialogOpen(): boolean {
  return Boolean(document.querySelector("[role='dialog'], [role='alertdialog']")) && !isComposerShowing()
}

/** Links the clipboard prompt already offered, kept across restarts and shared by open windows. */
function readSeen(): string[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(SEEN_KEY) ?? "[]")
    return Array.isArray(value) ? value.filter((key): key is string => typeof key === "string") : []
  } catch {
    return []
  }
}

function writeSeen(keys: string[]) {
  try {
    localStorage.setItem(SEEN_KEY, JSON.stringify(keys.slice(-SEEN_LIMIT)))
  } catch {
    // Without storage the same links may be offered again after a restart.
  }
}

/**
 * App-wide ways to start processing without going to the 处理 page:
 * Ctrl+N, pasting links outside text fields, dropping media files anywhere,
 * and (optional) noticing a new link on the clipboard when the window regains focus.
 */
export function GlobalIntake() {
  const { capabilities, online } = useAppAccess()
  const { open: composerOpen } = useComposerState()
  const [dragging, setDragging] = useState(false)
  const internalDrag = useRef(false)
  const dragDepth = useRef(0)
  const enabled = online && (capabilities.url_submission || capabilities.browser_file_upload)

  // Ctrl+N (Cmd+N): new processing. Browsers keep Ctrl+N for a new window; the desktop app gets it.
  useEffect(() => {
    if (!enabled) return
    const handler = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || event.shiftKey || event.altKey) return
      if (event.key.toLowerCase() !== "n" || otherDialogOpen()) return
      event.preventDefault()
      openComposer()
    }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  }, [enabled])

  // Ctrl+V outside a text field: links and media files start a new processing.
  useEffect(() => {
    if (!enabled) return
    const handler = (event: ClipboardEvent) => {
      if (isEditable(event.target) || otherDialogOpen() || !event.clipboardData) return
      const text = event.clipboardData.getData("text/plain")
      const files = Array.from(event.clipboardData.files).filter(isMediaFile)
      const hasLinks = capabilities.url_submission && parseSources(text, { allowPaths: capabilities.local_path_submission }).entries.length > 0
      if (!hasLinks && files.length === 0) return
      event.preventDefault()
      openComposer({ text: hasLinks ? text : undefined, files })
    }
    window.addEventListener("paste", handler)
    return () => window.removeEventListener("paste", handler)
  }, [capabilities.local_path_submission, capabilities.url_submission, enabled])

  // Drop media files (or a link dragged from a browser) anywhere in the window.
  useEffect(() => {
    if (!enabled) return
    const external = (event: DragEvent) => {
      if (internalDrag.current || !event.dataTransfer) return false
      const types = Array.from(event.dataTransfer.types)
      return types.includes("Files") || types.includes("text/uri-list")
    }
    const onDragStart = () => { internalDrag.current = true }
    const onDragEnd = () => { internalDrag.current = false }
    const onDragEnter = (event: DragEvent) => {
      if (!external(event)) return
      event.preventDefault()
      dragDepth.current += 1
      setDragging(true)
    }
    const onDragOver = (event: DragEvent) => {
      if (!external(event)) return
      event.preventDefault()
      if (event.dataTransfer) event.dataTransfer.dropEffect = "copy"
    }
    const onDragLeave = (event: DragEvent) => {
      if (!external(event)) return
      dragDepth.current = Math.max(0, dragDepth.current - 1)
      if (dragDepth.current === 0) setDragging(false)
    }
    const onDrop = (event: DragEvent) => {
      dragDepth.current = 0
      setDragging(false)
      if (internalDrag.current) {
        internalDrag.current = false
        return
      }
      if (!event.dataTransfer || !external(event)) return
      event.preventDefault()
      const dropped = Array.from(event.dataTransfer.files)
      const files = dropped.filter(isMediaFile)
      const text = dropped.length === 0 ? event.dataTransfer.getData("text/uri-list") || event.dataTransfer.getData("text/plain") : ""
      const hasLinks = Boolean(text) && parseSources(text).entries.length > 0
      if (files.length === 0 && !hasLinks) {
        if (dropped.length > 0) toast.error("只能添加音频或视频文件")
        return
      }
      openComposer({ files, text: hasLinks ? text : undefined })
    }
    window.addEventListener("dragstart", onDragStart)
    window.addEventListener("dragend", onDragEnd)
    window.addEventListener("dragenter", onDragEnter)
    window.addEventListener("dragover", onDragOver)
    window.addEventListener("dragleave", onDragLeave)
    window.addEventListener("drop", onDrop)
    return () => {
      window.removeEventListener("dragstart", onDragStart)
      window.removeEventListener("dragend", onDragEnd)
      window.removeEventListener("dragenter", onDragEnter)
      window.removeEventListener("dragover", onDragOver)
      window.removeEventListener("dragleave", onDragLeave)
      window.removeEventListener("drop", onDrop)
    }
  }, [enabled])

  // Coming back to MPP with a new link on the clipboard: offer to process it.
  useEffect(() => {
    if (!online || !capabilities.url_submission || typeof navigator.clipboard?.readText !== "function") return
    let busy = false
    const check = async () => {
      if (busy || !getPreferences().clipboardDetect || isComposerShowing() || document.visibilityState !== "visible" || !document.hasFocus()) return
      busy = true
      try {
        const text = (await navigator.clipboard.readText()).trim()
        if (!text || text.length > 20000) return
        // Each link is offered once. The same link copied again, or still on the clipboard
        // after a restart or in another window, stays quiet.
        const seen = readSeen()
        const unseen = parseSources(text).entries.filter((entry) => !seen.includes(entry.key))
        if (unseen.length === 0) return
        writeSeen([...seen, ...unseen.map((entry) => entry.key)])
        // Links that are already processed need no prompt.
        let fresh = unseen
        try {
          const { matches } = await api.archives.lookup(unseen.map((entry) => entry.source))
          fresh = unseen.filter((entry) => {
            const state = libraryStateOf(matches[entry.source])
            return !state || state.kind === "failed"
          })
        } catch {
          // Offer anyway.
        }
        if (fresh.length === 0) return
        toast(fresh.length === 1 ? "剪贴板里有一个链接" : `剪贴板里有 ${fresh.length} 个链接`, {
          // One line per link: a long id ends in … instead of wrapping over several lines.
          description: (
            <span className="flex flex-col">
              {fresh.slice(0, 2).map((entry) => (
                <span key={entry.key} className="truncate" title={entry.source}>{sourceLabel(entry.source)}</span>
              ))}
            </span>
          ),
          duration: 10000,
          action: { label: "新建处理", onClick: () => openComposer({ text: fresh.map((entry) => entry.source).join("\n") }) },
        })
      } catch {
        // Permission denied or clipboard unavailable: stay quiet; it can be turned off in 设置.
      } finally {
        busy = false
      }
    }
    const onFocus = () => { void check() }
    const onVisible = () => { if (document.visibilityState === "visible") void check() }
    window.addEventListener("focus", onFocus)
    document.addEventListener("visibilitychange", onVisible)
    const initial = window.setTimeout(() => void check(), 1500)
    return () => {
      window.clearTimeout(initial)
      window.removeEventListener("focus", onFocus)
      document.removeEventListener("visibilitychange", onVisible)
    }
  }, [capabilities.url_submission, online])

  if (!dragging) return null
  return (
    <div className="pointer-events-none fixed inset-0 z-[60] flex items-center justify-center bg-background/70 backdrop-blur-sm" aria-hidden="true">
      <div className="flex flex-col items-center gap-2 rounded-xl border-2 border-dashed border-primary bg-card px-10 py-8 text-center shadow-lg">
        <HugeiconsIcon icon={Upload01Icon} className="size-8 text-primary" />
        <p className="text-sm font-medium">{composerOpen ? "松开添加到列表" : "松开即可新建处理"}</p>
        <p className="text-xs text-muted-foreground">音频、视频文件或网页链接</p>
      </div>
    </div>
  )
}
