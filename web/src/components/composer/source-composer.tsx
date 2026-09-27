import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react"
import { HugeiconsIcon } from "@hugeicons/react"
import {
  Cancel01Icon, FileAudioIcon, FileVideoIcon, FolderOpenIcon, Loading03Icon, PlayIcon, Upload01Icon,
} from "@hugeicons/core-free-icons"

import { api, type LibraryMatch, type Task } from "@/lib/api"
import { useAppAccess } from "@/hooks/use-app-access-context"
import { useSubmitHistory } from "@/hooks/use-submit-history"
import { registerPageComposer, subscribeComposer, takeComposerIntake } from "@/lib/composer-store"
import { formatDuration } from "@/lib/format"
import { navigate } from "@/lib/router"
import { isMediaFile, parseSources, removeSourceText, type SourceEntry } from "@/lib/source-input"
import { sourceLabel } from "@/lib/task-display"
import { cn } from "@/lib/utils"
import { fetchSourceMeta, inspectBilibili, isMultiPart, libraryStateOf, type SourceMeta } from "@/components/composer/source-meta"
import { FolderQueueDialog } from "@/components/folder-queue-dialog"
import { PlatformIcon } from "@/components/platform-icon"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"

interface QueuedFile {
  id: string
  name: string
  size: number
  duration: number | null
  stagingId: string
  stagingPath: string
  uploading: boolean
  error: string
}

interface PartSelection {
  selectedIds: string[]
  /** The part list has been shown, so submitting uses the selection as is */
  expanded: boolean
}

type SubtitleStrategy = "auto" | "force_asr"

function formatFileSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

function getMediaDuration(file: File): Promise<number | null> {
  return new Promise((resolve) => {
    if (typeof URL.createObjectURL !== "function") {
      resolve(null)
      return
    }
    const url = URL.createObjectURL(file)
    const element = file.type.startsWith("video/") ? document.createElement("video") : document.createElement("audio")
    element.preload = "metadata"
    element.onloadedmetadata = () => { URL.revokeObjectURL(url); resolve(Number.isFinite(element.duration) ? element.duration : null) }
    element.onerror = () => { URL.revokeObjectURL(url); resolve(null) }
    element.src = url
  })
}

const isVideoName = (name: string) => /\.(mp4|mkv|avi|webm|mov|flv|wmv|m4v|ts)$/i.test(name)

interface SourceComposerProps {
  mode: "dialog" | "page"
  /** Called with the created tasks after a successful submit */
  onSubmitted?: (tasks: Task[]) => void
  onCancel?: () => void
  /** Called before the composer navigates elsewhere (e.g. 打开 an existing archive) */
  onNavigate?: () => void
}

/**
 * Links (one per line), dropped or chosen files, and processing options in one place.
 * Rendered in the new-processing dialog and on the 处理 page.
 */
export function SourceComposer({ mode, onSubmitted, onCancel, onNavigate }: SourceComposerProps) {
  const { capabilities, online } = useAppAccess()
  const submitHistory = useSubmitHistory()
  const [text, setText] = useState("")
  const [files, setFiles] = useState<QueuedFile[]>([])
  const [strategy, setStrategy] = useState<SubtitleStrategy>("auto")
  const [numSpeakers, setNumSpeakers] = useState("")
  const [hotwords, setHotwords] = useState<string[]>([])
  const [hotwordInput, setHotwordInput] = useState("")
  const [meta, setMeta] = useState<Record<string, SourceMeta | null | "loading">>({})
  const [library, setLibrary] = useState<Record<string, LibraryMatch[]>>({})
  const [parts, setParts] = useState<Record<string, PartSelection>>({})
  const [forced, setForced] = useState<Set<string>>(() => new Set())
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState("")
  const [showFolderDialog, setShowFolderDialog] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const folderInputRef = useRef<HTMLInputElement>(null)
  const hotwordInputRef = useRef<HTMLInputElement>(null)
  const abortControllers = useRef<Map<string, AbortController>>(new Map())
  const metaRequested = useRef<Set<string>>(new Set())
  const libraryRequested = useRef<Set<string>>(new Set())

  const { entries, unrecognized } = useMemo(
    () => parseSources(text, { allowPaths: capabilities.local_path_submission }),
    [text, capabilities.local_path_submission],
  )
  const urlEntries = useMemo(() => entries.filter((entry) => entry.kind === "url"), [entries])

  // ---------- files ----------
  const addFiles = useCallback((incoming: File[]) => {
    const media = incoming.filter(isMediaFile)
    if (media.length === 0) {
      if (incoming.length) setError("只能添加音频或视频文件。")
      return
    }
    if (!capabilities.browser_file_upload) {
      setError("当前连接不支持从浏览器上传文件。")
      return
    }
    setError("")
    for (const file of media) {
      const id = `${file.name}-${Date.now()}-${Math.random()}`
      const controller = new AbortController()
      abortControllers.current.set(id, controller)
      setFiles((current) => [...current, {
        id, name: file.name, size: file.size, duration: null, stagingId: "", stagingPath: "", uploading: true, error: "",
      }])
      void Promise.all([getMediaDuration(file), api.pipeline.stage(file, controller.signal)])
        .then(([duration, staged]) => {
          setFiles((current) => current.map((item) => item.id === id
            ? { ...item, duration, stagingId: staged.staging_id, stagingPath: staged.path, uploading: false }
            : item))
        })
        .catch(() => {
          if (controller.signal.aborted) setFiles((current) => current.filter((item) => item.id !== id))
          else setFiles((current) => current.map((item) => item.id === id ? { ...item, uploading: false, error: "上传失败" } : item))
        })
        .finally(() => abortControllers.current.delete(id))
    }
  }, [capabilities.browser_file_upload])

  const removeFile = (id: string) => {
    abortControllers.current.get(id)?.abort()
    abortControllers.current.delete(id)
    setFiles((current) => {
      const target = current.find((item) => item.id === id)
      if (target?.stagingId) api.pipeline.deleteStaged(target.stagingId).catch(() => {})
      return current.filter((item) => item.id !== id)
    })
  }

  // ---------- text and files sent from elsewhere (paste, drop, Ctrl+N, share) ----------
  useEffect(() => {
    const drain = () => {
      const taken = takeComposerIntake()
      if (taken.length === 0) return
      for (const item of taken) {
        if (item.text) {
          const incoming = item.text.trim()
          setText((current) => current.trim() ? `${current.trimEnd()}\n${incoming}` : incoming)
        }
        if (item.files?.length) addFiles(item.files)
      }
      textareaRef.current?.focus()
    }
    const unsubscribe = subscribeComposer(drain)
    queueMicrotask(drain)
    return unsubscribe
  }, [addFiles])

  useEffect(() => {
    if (mode === "page") return registerPageComposer()
  }, [mode])

  // ---------- what each link is: title, duration, parts, subtitles ----------
  useEffect(() => {
    const pending = urlEntries.filter((entry) => !metaRequested.current.has(entry.key))
    if (pending.length === 0) return
    const timer = window.setTimeout(() => {
      for (const entry of pending) metaRequested.current.add(entry.key)
      setMeta((current) => ({ ...current, ...Object.fromEntries(pending.map((entry) => [entry.key, "loading" as const])) }))
      for (const entry of pending) {
        void fetchSourceMeta(entry).then((result) => {
          setMeta((current) => ({ ...current, [entry.key]: result }))
          if (isMultiPart(result?.collection)) {
            const items = result.collection.items
            setParts((current) => current[entry.key] ? current : {
              ...current, [entry.key]: { selectedIds: items.map((item) => item.id), expanded: false },
            })
          }
        })
      }
    }, 500)
    return () => window.clearTimeout(timer)
  }, [urlEntries])

  // ---------- already in the library? ----------
  useEffect(() => {
    const pending = urlEntries.filter((entry) => !libraryRequested.current.has(entry.key))
    if (pending.length === 0) return
    const timer = window.setTimeout(() => {
      for (const entry of pending) libraryRequested.current.add(entry.key)
      api.archives.lookup(pending.map((entry) => entry.source))
        .then(({ matches }) => {
          setLibrary((current) => ({
            ...current,
            ...Object.fromEntries(pending.map((entry) => [entry.key, matches[entry.source] ?? []])),
          }))
        })
        .catch(() => {
          // Offline or an older daemon: submit without the duplicate hint.
          for (const entry of pending) libraryRequested.current.delete(entry.key)
        })
    }, 350)
    return () => window.clearTimeout(timer)
  }, [urlEntries])

  const libraryOf = (entry: SourceEntry) => libraryStateOf(library[entry.key])
  const isSkipped = (entry: SourceEntry) => {
    const state = libraryOf(entry)
    return Boolean(state && state.kind !== "failed" && !forced.has(entry.key))
  }

  const included = entries.filter((entry) => !isSkipped(entry))
  const skippedCount = entries.length - included.length
  const readyFiles = files.filter((item) => item.stagingPath && !item.uploading && !item.error)
  const uploadingCount = files.filter((item) => item.uploading).length
  const countFor = (entry: SourceEntry) => {
    const collection = (meta[entry.key] as SourceMeta | null | undefined)?.collection
    const selection = parts[entry.key]
    return isMultiPart(collection) && selection ? selection.selectedIds.length : 1
  }
  const total = readyFiles.length + included.reduce((sum, entry) => sum + countFor(entry), 0)
  const canSubmit = online && total > 0 && !submitting && uploadingCount === 0
    && (included.length === 0 || capabilities.url_submission)

  const buildOptions = () => {
    const options: Record<string, unknown> = { force_asr: strategy === "force_asr" }
    const speakers = Number.parseInt(numSpeakers, 10)
    if (speakers > 0) options.num_speakers = speakers
    if (hotwords.length > 0) options.hotwords = hotwords
    return options
  }

  const reset = () => {
    setText("")
    setFiles([])
    setParts({})
    setForced(new Set())
    setError("")
  }

  const handleSubmit = async () => {
    if (!canSubmit) return
    setSubmitting(true)
    setError("")
    try {
      // Multi-part Bilibili videos and seasons: show the part list before the first submit.
      const selections = { ...parts }
      for (const entry of included) {
        if (entry.platform !== "bilibili" || entry.kind !== "url") continue
        const collection = (meta[entry.key] as SourceMeta | null | undefined)?.collection ?? await inspectBilibili(entry.source)
        if (!isMultiPart(collection)) continue
        const selection = selections[entry.key] ?? { selectedIds: collection.items.map((item) => item.id), expanded: false }
        if (!selection.expanded) {
          setMeta((current) => ({
            ...current,
            [entry.key]: { ...((current[entry.key] as SourceMeta | null) ?? {}), title: collection.title, collection },
          }))
          setParts((current) => ({ ...current, [entry.key]: { ...selection, expanded: true } }))
          setError(`「${collection.title || "这个合集"}」有 ${collection.items.length} 集，选好要处理的分集后再点开始处理。`)
          return
        }
        selections[entry.key] = selection
      }

      const sources = readyFiles.map((item) => item.stagingPath)
      for (const entry of included) {
        const collection = (meta[entry.key] as SourceMeta | null | undefined)?.collection ?? (
          entry.platform === "bilibili" ? await inspectBilibili(entry.source) : null)
        const selection = selections[entry.key]
        if (isMultiPart(collection) && selection) {
          const chosen = new Set(selection.selectedIds)
          sources.push(...collection.items.filter((item) => chosen.has(item.id)).map((item) => item.url))
        } else {
          sources.push(entry.source)
        }
      }
      if (sources.length === 0) return

      const tasks = await api.tasks.createBatch(sources, buildOptions())
      for (const entry of included) if (entry.kind === "url") submitHistory.add(entry.source)
      reset()
      onSubmitted?.(tasks)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "提交失败")
    } finally {
      setSubmitting(false)
    }
  }

  const handleTextKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault()
      void handleSubmit()
    }
  }

  const addHotword = (word: string) => {
    const value = word.replace(/[,，、]/g, "").trim()
    if (value && !hotwords.includes(value)) setHotwords((current) => [...current, value])
  }

  const handleHotwordKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter" || event.key === "," || event.key === "、" || event.key === "，") {
      event.preventDefault()
      addHotword(hotwordInput)
      setHotwordInput("")
    } else if (event.key === "Backspace" && !hotwordInput && hotwords.length > 0) {
      setHotwords((current) => current.slice(0, -1))
    }
  }

  const openArchive = (match: LibraryMatch) => {
    onNavigate?.()
    const tid = match.task_id ? `&taskId=${encodeURIComponent(match.task_id)}` : ""
    navigate(`#/result/archive?path=${encodeURIComponent(match.path)}${tid}`)
  }

  const toggleForced = (key: string) => {
    setForced((current) => {
      const next = new Set(current)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const togglePart = (key: string, id: string) => {
    setParts((current) => {
      const selection = current[key]
      if (!selection) return current
      const selected = selection.selectedIds.includes(id)
      return {
        ...current,
        [key]: { ...selection, selectedIds: selected ? selection.selectedIds.filter((item) => item !== id) : [...selection.selectedIds, id] },
      }
    })
  }

  const setAllParts = (key: string, ids: string[]) => {
    setParts((current) => current[key] ? { ...current, [key]: { ...current[key], selectedIds: ids } } : current)
  }

  const hasRows = entries.length > 0 || files.length > 0

  return (
    <div className="flex min-h-0 flex-col gap-3">
      <FolderQueueDialog
        open={showFolderDialog && capabilities.filesystem_browse}
        onOpenChange={setShowFolderDialog}
        options={buildOptions()}
        onSubmitted={(taskId) => {
          onNavigate?.()
          navigate(taskId ? `#/result/task/${taskId}` : "#/files")
        }}
      />

      <div className="flex flex-col gap-1.5">
        <Textarea
          ref={textareaRef}
          autoFocus
          value={text}
          onChange={(event) => {
            setText(event.target.value)
            setError("")
          }}
          onKeyDown={handleTextKeyDown}
          placeholder="粘贴视频或网页链接，一行一个"
          aria-label="链接"
          rows={3}
          disabled={submitting || !online}
          spellCheck={false}
          autoCapitalize="none"
          className="max-h-48 min-h-20 resize-none text-sm"
        />
        <p className="text-xs leading-relaxed text-muted-foreground">
          支持 B站、YouTube、小红书、X、知乎、小宇宙、播客和网页链接，也可以粘贴带链接的分享文字。
          {capabilities.browser_file_upload && "音视频文件直接拖到窗口里。"}
        </p>
      </div>

      {hasRows && (
        <ul className="max-h-[38vh] min-h-0 divide-y overflow-y-auto rounded-lg border" aria-label="待处理条目">
          {entries.map((entry) => {
            const info = meta[entry.key]
            const details = info && info !== "loading" ? info : null
            const state = libraryOf(entry)
            const skipped = isSkipped(entry)
            const collection = details?.collection
            const multi = isMultiPart(collection)
            const selection = parts[entry.key]
            return (
              <li key={entry.key} className={cn("px-3 py-2 text-[13px]", skipped && "bg-muted/40")}>
                <div className="flex items-start gap-2.5">
                  <span className="mt-0.5 flex size-4 shrink-0 items-center justify-center">
                    {entry.platform
                      ? <PlatformIcon platform={entry.platform} className="size-4" iconOnly />
                      : <HugeiconsIcon icon={isVideoName(entry.source) ? FileVideoIcon : FileAudioIcon} className="size-4 text-muted-foreground" />}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className={cn("truncate font-medium", skipped && "text-muted-foreground")} title={entry.source}>
                      {details?.title || sourceLabel(entry.source)}
                    </p>
                    <p className="flex min-w-0 flex-wrap items-center gap-x-1.5 text-xs text-muted-foreground">
                      {info === "loading" && <span className="inline-flex items-center gap-1"><HugeiconsIcon icon={Loading03Icon} className="size-3 animate-spin" />读取信息…</span>}
                      {entry.kind === "path" && <span>本机文件</span>}
                      {details?.uploader && <span className="max-w-48 truncate">{details.uploader}</span>}
                      {details?.duration ? <span className="tabular-nums">{formatDuration(details.duration)}</span> : null}
                      {multi && <span>{collection.collection_type === "ugc_season" ? "合集" : "分 P"} · {collection.items.length} 集</span>}
                      {details?.subtitles?.length ? <span title={details.subtitles.join(", ")}>有平台字幕</span>
                        : details?.autoCaptions ? <span>有自动字幕</span> : null}
                    </p>
                    {state && (
                      <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs">
                        <span className={state.kind === "failed" ? "text-muted-foreground" : "text-amber-700 dark:text-amber-400"}>
                          {state.kind === "completed" ? "已在库中"
                            : state.kind === "processing" ? "正在处理"
                              : state.kind === "paused" ? "已暂停，在活动里可以继续"
                                : `之前失败过 ${state.count} 次，会重新处理`}
                        </span>
                        {state.kind !== "failed" && (
                          <>
                            <button type="button" className="text-primary hover:underline" onClick={() => openArchive(state.match)}>
                              打开
                            </button>
                            <button type="button" className="text-muted-foreground hover:text-foreground hover:underline" onClick={() => toggleForced(entry.key)}>
                              {skipped ? "仍然重新处理" : "不重复处理"}
                            </button>
                          </>
                        )}
                      </p>
                    )}
                    {multi && selection && (
                      <div className="mt-1">
                        <button
                          type="button"
                          className="text-xs text-primary hover:underline"
                          onClick={() => setParts((current) => ({ ...current, [entry.key]: { ...selection, expanded: !selection.expanded } }))}
                        >
                          已选 {selection.selectedIds.length}/{collection.items.length} 集 · {selection.expanded ? "收起" : "选择分集"}
                        </button>
                        {selection.expanded && (
                          <div className="mt-1.5 rounded-md border bg-background">
                            <div className="flex items-center gap-3 border-b px-2 py-1 text-xs">
                              <span className="min-w-0 flex-1 truncate text-muted-foreground">选择要处理的分集</span>
                              <button type="button" className="text-muted-foreground hover:text-foreground" onClick={() => setAllParts(entry.key, collection.items.map((item) => item.id))}>全选</button>
                              <button type="button" className="text-muted-foreground hover:text-foreground" onClick={() => setAllParts(entry.key, [])}>清空选择</button>
                            </div>
                            <div className="max-h-56 overflow-y-auto py-1">
                              {collection.items.map((item, index) => (
                                <label key={item.id} className="flex cursor-pointer items-center gap-2 px-2 py-1 text-xs hover:bg-muted/60">
                                  <input
                                    type="checkbox"
                                    checked={selection.selectedIds.includes(item.id)}
                                    onChange={() => togglePart(entry.key, item.id)}
                                    aria-label={`选择 ${item.title}`}
                                    className="size-3.5 shrink-0 accent-primary"
                                  />
                                  <span className="w-6 shrink-0 text-right tabular-nums text-muted-foreground">{index + 1}</span>
                                  <span className="min-w-0 flex-1 truncate">
                                    {item.title}
                                    {item.section && <span className="ml-1.5 text-muted-foreground">{item.section}</span>}
                                  </span>
                                  {item.duration != null && <span className="shrink-0 tabular-nums text-muted-foreground">{formatDuration(item.duration)}</span>}
                                </label>
                              ))}
                            </div>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                  <button
                    type="button"
                    aria-label={`移除 ${entry.source}`}
                    className="rounded p-0.5 text-muted-foreground/60 hover:text-foreground"
                    onClick={() => setText((current) => removeSourceText(current, entry))}
                  >
                    <HugeiconsIcon icon={Cancel01Icon} className="size-3.5" />
                  </button>
                </div>
              </li>
            )
          })}
          {files.map((item) => (
            <li key={item.id} className={cn("flex items-center gap-2.5 px-3 py-2 text-[13px]", item.error && "bg-destructive/10 text-destructive")}>
              <HugeiconsIcon
                icon={item.uploading ? Loading03Icon : isVideoName(item.name) ? FileVideoIcon : FileAudioIcon}
                className={cn("size-4 shrink-0 text-muted-foreground", item.uploading && "animate-spin")}
              />
              <span className="min-w-0 flex-1 truncate font-medium">{item.name}</span>
              <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                {item.error || (item.uploading ? "上传中…" : `${formatFileSize(item.size)}${item.duration != null ? ` · ${formatDuration(item.duration)}` : ""}`)}
              </span>
              <button type="button" aria-label={`移除 ${item.name}`} className="rounded p-0.5 text-muted-foreground/60 hover:text-foreground" onClick={() => removeFile(item.id)}>
                <HugeiconsIcon icon={Cancel01Icon} className="size-3.5" />
              </button>
            </li>
          ))}
        </ul>
      )}

      {unrecognized.length > 0 && (
        <p className="text-xs text-muted-foreground">有 {unrecognized.length} 行没有找到链接，会被忽略。</p>
      )}

      {(capabilities.browser_file_upload || capabilities.filesystem_browse || capabilities.browser_folder_upload) && (
        <div className="flex flex-wrap items-center gap-2">
          {capabilities.browser_file_upload && (
            <Button type="button" variant="outline" size="sm" onClick={() => fileInputRef.current?.click()}>
              <HugeiconsIcon icon={Upload01Icon} className="size-3.5" />
              选择文件
            </Button>
          )}
          {(capabilities.filesystem_browse || capabilities.browser_folder_upload) && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => capabilities.filesystem_browse ? setShowFolderDialog(true) : folderInputRef.current?.click()}
            >
              <HugeiconsIcon icon={FolderOpenIcon} className="size-3.5" />
              选择文件夹
            </Button>
          )}
          <input
            ref={fileInputRef}
            type="file"
            accept="video/*,audio/*"
            multiple
            className="hidden"
            onChange={(event) => {
              if (event.target.files) { addFiles(Array.from(event.target.files)); event.target.value = "" }
            }}
          />
          <input
            ref={folderInputRef}
            type="file"
            accept="video/*,audio/*"
            multiple
            className="hidden"
            {...({ webkitdirectory: "", directory: "" } as React.InputHTMLAttributes<HTMLInputElement>)}
            onChange={(event) => {
              if (event.target.files) { addFiles(Array.from(event.target.files)); event.target.value = "" }
            }}
          />
        </div>
      )}

      {/* Options sit above the submit button so they are seen before starting */}
      <fieldset className="grid gap-x-4 gap-y-2 rounded-lg border bg-muted/30 px-3 py-2.5 sm:grid-cols-[auto_auto_minmax(0,1fr)]">
        <legend className="sr-only">处理选项</legend>
        <div className="flex flex-col gap-1">
          <span className="text-xs font-medium">字幕来源</span>
          <div className="inline-flex rounded-md border bg-background p-0.5" role="radiogroup" aria-label="字幕来源">
            {([["auto", "自动"], ["force_asr", "强制 ASR"]] as const).map(([value, label]) => (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={strategy === value}
                onClick={() => setStrategy(value)}
                className={cn(
                  "h-7 rounded px-2.5 text-xs transition-colors",
                  strategy === value ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground",
                )}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium">说话人数</span>
          <Input
            type="number"
            min="1"
            max="20"
            value={numSpeakers}
            onChange={(event) => setNumSpeakers(event.target.value)}
            placeholder="自动"
            className="h-8 w-20 text-xs"
          />
        </label>
        <div className="flex min-w-0 flex-col gap-1">
          <span className="text-xs font-medium">热词</span>
          <div
            className="flex min-h-8 cursor-text flex-wrap items-center gap-1 rounded-md border bg-background px-1.5 py-1"
            onClick={() => hotwordInputRef.current?.focus()}
          >
            {hotwords.map((tag) => (
              <span key={tag} className="inline-flex items-center gap-0.5 rounded bg-primary/10 px-1.5 py-0.5 text-xs font-medium text-primary">
                {tag}
                <button type="button" aria-label={`删除热词 ${tag}`} onClick={(event) => { event.stopPropagation(); setHotwords((current) => current.filter((item) => item !== tag)) }} className="rounded-full p-0.5 hover:bg-primary/20">
                  <HugeiconsIcon icon={Cancel01Icon} className="size-2.5" />
                </button>
              </span>
            ))}
            <input
              ref={hotwordInputRef}
              value={hotwordInput}
              onChange={(event) => setHotwordInput(event.target.value)}
              onKeyDown={handleHotwordKeyDown}
              onBlur={() => { addHotword(hotwordInput); setHotwordInput("") }}
              placeholder={hotwords.length === 0 ? "人名、术语，回车添加" : ""}
              aria-label="热词"
              className="min-w-24 flex-1 bg-transparent py-0.5 text-xs outline-none placeholder:text-muted-foreground/70"
            />
          </div>
        </div>
        <p className="text-xs text-muted-foreground sm:col-span-3">
          {strategy === "auto"
            ? "自动：平台有字幕就直接用，没有再转写；之后区分说话人、润色，生成摘要和导图。"
            : "强制 ASR：忽略平台字幕，下载后自己转写（人声分离 → 识别 → 说话人 → 润色 → 摘要和导图）。"}
          {total > 1 && ` 选项对全部 ${total} 项生效。`}
        </p>
      </fieldset>

      {error && <p className="text-sm text-destructive" role="alert">{error}</p>}
      {!online && <p className="text-sm text-muted-foreground">连接服务器后才能提交。</p>}

      <div className="flex flex-wrap items-center gap-2">
        <p className="min-w-0 flex-1 text-xs text-muted-foreground">
          {uploadingCount > 0 ? `${uploadingCount} 个文件上传中…`
            : skippedCount > 0 ? `${skippedCount} 个已在库中，不会重复处理`
              : mode === "dialog" ? "Ctrl+Enter 开始处理" : ""}
        </p>
        {mode === "dialog" && (
          <Button type="button" variant="ghost" onClick={onCancel} disabled={submitting}>取消</Button>
        )}
        <Button type="button" disabled={!canSubmit} onClick={() => void handleSubmit()} className="min-w-28">
          <HugeiconsIcon icon={submitting ? Loading03Icon : PlayIcon} className={cn("size-4", submitting && "animate-spin")} />
          {submitting ? "提交中…" : total > 1 ? `开始处理（${total} 个）` : "开始处理"}
        </Button>
      </div>
    </div>
  )
}
