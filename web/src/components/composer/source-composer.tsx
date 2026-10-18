import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react"
import { HugeiconsIcon } from "@hugeicons/react"
import {
  Cancel01Icon, FileAudioIcon, FileVideoIcon, FolderOpenIcon, Loading03Icon, PlayIcon, Upload01Icon,
} from "@hugeicons/core-free-icons"

import { api, type ImportItemResult, type ImportSummary, type LibraryMatch, type Task } from "@/lib/api"
import { useAppAccess } from "@/hooks/use-app-access-context"
import { libraryChanged, useLibraryDirectory } from "@/hooks/use-library-directory"
import { useSubmitHistory } from "@/hooks/use-submit-history"
import { registerPageComposer, subscribeComposer, takeComposerIntake } from "@/lib/composer-store"
import { formatDuration } from "@/lib/format"
import { navigate } from "@/lib/router"
import { isMediaFile, parseSources, removeSourceText, type SourceEntry } from "@/lib/source-input"
import { sourceLabel } from "@/lib/task-display"
import { cn } from "@/lib/utils"
import { fetchSourceMeta, isMultiPart, libraryStateOf, type SourceMeta } from "@/components/composer/source-meta"
import { CollectionSelector } from "@/components/composer/collection-selector"
import { FolderQueueDialog } from "@/components/folder-queue-dialog"
import { PlatformIcon } from "@/components/platform-icon"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { WorkflowControls } from "@/components/workflow-controls"
import { DEFAULT_WORKFLOW, workflowDefaults, type WorkflowConfig } from "@/lib/workflow"

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
  fallbackSource?: boolean
}

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
  onSubmitted?: (tasks: Task[], summary?: ImportSummary) => void
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
  const { folders } = useLibraryDirectory()
  const [destination, setDestination] = useState(() => new URLSearchParams(window.location.hash.split("?")[1] ?? "").get("folder") ?? "auto")
  const [moveExisting, setMoveExisting] = useState(false)
  const [collectionFolders, setCollectionFolders] = useState<Set<string>>(() => new Set())
  const submitHistory = useSubmitHistory()
  const [text, setText] = useState("")
  const [files, setFiles] = useState<QueuedFile[]>([])
  const [defaultWorkflow, setDefaultWorkflow] = useState(DEFAULT_WORKFLOW)
  const [workflowOverride, setWorkflowOverride] = useState<WorkflowConfig | null>(null)
  const [workflowLoaded, setWorkflowLoaded] = useState(false)
  const [workflowError, setWorkflowError] = useState(false)
  const workflow = workflowOverride ?? defaultWorkflow
  const [numSpeakers, setNumSpeakers] = useState("")
  const [hotwords, setHotwords] = useState<string[]>([])
  const [hotwordInput, setHotwordInput] = useState("")
  const [meta, setMeta] = useState<Record<string, SourceMeta | null | "loading">>({})
  const [library, setLibrary] = useState<Record<string, LibraryMatch[] | null>>({})
  const [parts, setParts] = useState<Record<string, PartSelection>>({})
  const [activeCollection, setActiveCollection] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [collectionNotice, setCollectionNotice] = useState("")
  const [results, setResults] = useState<Record<string, ImportItemResult>>({})
  const [summary, setSummary] = useState<ImportSummary | null>(null)
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

  useEffect(() => {
    let active = true
    const load = async () => {
      try {
        const settings = await api.settings.get()
        if (active) { setDefaultWorkflow(workflowDefaults(settings ?? {})); setWorkflowError(false) }
      } catch {
        if (active) setWorkflowError(true)
      } finally {
        if (active) setWorkflowLoaded(true)
      }
    }
    void load()
    window.addEventListener("mpp:settings-change", load)
    return () => { active = false; window.removeEventListener("mpp:settings-change", load) }
  }, [])

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
      if (target?.stagingId && !results[target.stagingPath]?.task) api.pipeline.deleteStaged(target.stagingId).catch(() => {})
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
            const currentId = result.collection.current_item_id
            const hasCurrent = result.collection.items.some((item) => item.id === currentId)
            setParts((current) => current[entry.key] ? current : {
              ...current, [entry.key]: { selectedIds: hasCurrent && currentId ? [currentId] : [], fallbackSource: !hasCurrent },
            })
          }
        })
      }
    }, 500)
    return () => window.clearTimeout(timer)
  }, [urlEntries])

  // Lookup the actual directory members as well as ordinary pasted links.
  const lookupSources = useMemo(() => [...new Set(urlEntries.flatMap((entry) => {
    const info = meta[entry.key]
    return info && info !== "loading" && isMultiPart(info.collection)
      ? [entry.source, ...info.collection.items.map((item) => item.url)] : [entry.source]
  }))], [urlEntries, meta])

  const lookup = useCallback(async (sources: string[]) => {
    const updates: Record<string, LibraryMatch[] | null> = {}
    for (let offset = 0; offset < sources.length; offset += 200) {
      const batch = sources.slice(offset, offset + 200)
      try {
        const response = await api.archives.lookup(batch)
        for (const source of batch) updates[source] = response.errors?.[source] ? null : response.matches[source] ?? []
      } catch {
        for (const source of batch) updates[source] = null
      }
    }
    setLibrary((current) => ({ ...current, ...updates }))
    return updates
  }, [])

  useEffect(() => {
    const pending = lookupSources.filter((source) => !libraryRequested.current.has(source))
    if (pending.length === 0) return
    const timer = window.setTimeout(() => {
      for (const source of pending) libraryRequested.current.add(source)
      void lookup(pending)
    }, 350)
    return () => window.clearTimeout(timer)
  }, [lookupSources, lookup])

  const libraryOf = (entry: SourceEntry) => libraryStateOf(library[entry.source] ?? undefined)
  const sourcesFor = (entry: SourceEntry) => {
    const info = meta[entry.key]
    const collection = info && info !== "loading" ? info.collection : undefined
    const selection = parts[entry.key]
    if (isMultiPart(collection) && selection && !selection.fallbackSource) {
      const ids = new Set(selection.selectedIds)
      return collection.items.filter((item) => ids.has(item.id)).map((item) => item.url)
    }
    return [entry.source]
  }

  const readyFiles = files.filter((item) => item.stagingPath && !item.uploading && !item.error)
  const uploadingCount = files.filter((item) => item.uploading).length
  const selectedSources = [...new Set([...entries.flatMap(sourcesFor), ...readyFiles.map((item) => item.stagingPath)])]
  const unfinishedSources = selectedSources.filter((source) => !results[source] || results[source].outcome === "failed")
  const total = unfinishedSources.length
  const preview = unfinishedSources.reduce((counts, source) => {
    if (!/^https?:\/\//.test(source)) counts.fresh += 1
    else if (library[source] == null) counts.unknown += 1
    else {
      const state = libraryStateOf(library[source] ?? undefined)
      if (!state) counts.fresh += 1
      else if (state.kind === "completed") counts.reused += 1
      else if (state.kind === "processing") counts.active += 1
      else counts.needsAction += 1
    }
    return counts
  }, { fresh: 0, reused: 0, active: 0, needsAction: 0, unknown: 0 })
  const canSubmit = online && workflowLoaded && total > 0 && !submitting && uploadingCount === 0
    && (entries.length === 0 || capabilities.url_submission)

  const buildOptions = () => {
    const options: Record<string, unknown> = { force_asr: true, workflow }
    const speakers = Number.parseInt(numSpeakers, 10)
    if (speakers > 0) options.num_speakers = speakers
    if (hotwords.length > 0) options.hotwords = hotwords
    return options
  }

  const reset = () => {
    setText("")
    setFiles([])
    setParts({})
    setMeta({})
    setLibrary({})
    metaRequested.current.clear()
    libraryRequested.current.clear()
    setActiveCollection(null)
    setCollectionFolders(new Set())
    setResults({})
    setSummary(null)
    setError("")
    setWorkflowOverride(null)
  }

  const handleSubmit = async () => {
    if (!canSubmit) return
    setSubmitting(true)
    setError("")
    try {
      // Freeze sources and options before asynchronous work; expansion is never confirmation.
      const sources = [...unfinishedSources]
      const options = buildOptions()
      const importDestination = {
        ...(destination === "auto" ? {} : { folder_id: destination === "none" ? null : destination }),
        ...(moveExisting ? { move_existing: true } : {}),
        ...(collectionFolders.size ? { collection_folders: [...collectionFolders].filter((source) => urlEntries.some((entry) => entry.source === source)).map((source) => ({ source, create_folder: true })) } : {}),
      }
      await lookup(sources.filter((source) => /^https?:\/\//.test(source)))
      const collected = { ...results }
      for (let offset = 0; offset < sources.length; offset += 100) {
        const batch = sources.slice(offset, offset + 100)
        try {
          const response = Object.keys(importDestination).length
            ? await api.tasks.importSources(batch, options, importDestination)
            : await api.tasks.importSources(batch, options)
          for (const item of response.items) collected[item.source] = item
          for (const source of batch) {
            if (!response.items.some((item) => item.source === source)) {
              collected[source] = { source, content_key: null, outcome: "failed", error: "提交结果未确认，请重试" }
            }
          }
        } catch (reason) {
          // The server may have accepted this batch. Retrying import is safe.
          for (const source of sources.slice(offset)) collected[source] = {
            source, content_key: null, outcome: "failed", error: reason instanceof Error ? reason.message : "提交结果未确认，请重试",
          }
          break
        }
        setResults({ ...collected })
      }
      setResults(collected)
      const items = selectedSources.map((source) => collected[source]).filter(Boolean)
      const counts: ImportSummary = { created: 0, reused: 0, active: 0, needsAction: 0, failed: 0 }
      for (const item of items) {
        if (item.folder_error || ["kept_other", "ambiguous"].includes(item.folder_outcome ?? "")) counts.folderWarnings = (counts.folderWarnings ?? 0) + 1
        if (item.outcome === "created") counts.created += 1
        else if (item.outcome === "failed") counts.failed += 1
        else if (item.outcome === "skipped") counts.needsAction += 1
        else if (item.match?.processing || ["pending", "queued", "processing"].includes(item.task?.status ?? item.match?.status ?? "")) counts.active += 1
        else counts.reused += 1
      }
      setSummary(counts)
      libraryChanged()
      for (const entry of entries) if (entry.kind === "url") submitHistory.add(entry.source)
      if (!counts.failed && !counts.needsAction && !counts.folderWarnings) {
        const tasks = items.filter((item) => item.outcome === "created" && item.task).map((item) => item.task!)
        reset()
        onSubmitted?.(tasks, counts)
      }
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
    if (match.task_id && (!match.path || match.status !== "completed")) {
      navigate(`#/result/task/${match.task_id}`)
      return
    }
    const tid = match.task_id ? `&taskId=${encodeURIComponent(match.task_id)}` : ""
    navigate(`#/result/archive?path=${encodeURIComponent(match.path)}${tid}`)
  }

  const rerun = async (entry: SourceEntry, match: LibraryMatch) => {
    if (submitting) return
    setSubmitting(true)
    setError("")
    try {
      if (match.task_id) {
        await api.tasks.fullRerun(match.task_id)
        onNavigate?.()
        navigate(`#/result/task/${match.task_id}`)
      } else {
        const task = await api.tasks.create(entry.source, buildOptions())
        onSubmitted?.([task], { created: 1, reused: 0, active: 0, needsAction: 0, failed: 0 })
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "重新处理失败")
    } finally {
      setSubmitting(false)
    }
  }

  const refreshCollection = async (entry: SourceEntry) => {
    setRefreshing(true)
    setCollectionNotice("")
    try {
      const refreshed = await fetchSourceMeta(entry, true)
      if (!refreshed?.collection?.is_collection) {
        setCollectionNotice("刷新失败，已保留原目录和选择。可再次刷新。")
        return
      }
      const fresh = refreshed.collection
      const ids = new Set(fresh.items.map((item) => item.id))
      const previous = parts[entry.key]
      const removed = previous?.selectedIds.filter((id) => !ids.has(id)).length ?? 0
      setMeta((current) => ({ ...current, [entry.key]: refreshed }))
      setParts((current) => ({ ...current, [entry.key]: {
        ...current[entry.key], selectedIds: (current[entry.key]?.selectedIds ?? []).filter((id) => ids.has(id)),
      } }))
      setCollectionNotice(removed ? `${removed} 个已选视频已不在目录中。新增视频未自动选择。` : "目录已刷新，保留原选择。新增视频未自动选择。")
      await lookup([entry.source, ...fresh.items.map((item) => item.url)])
    } finally {
      setRefreshing(false)
    }
  }

  const hasRows = entries.length > 0 || files.length > 0
  const selectedEntry = entries.find((entry) => entry.key === activeCollection)
  const selectedMeta = selectedEntry ? meta[selectedEntry.key] : null
  if (selectedEntry && selectedMeta && selectedMeta !== "loading" && selectedMeta.collection) {
    return <CollectionSelector
      collection={selectedMeta.collection}
      selectedIds={parts[selectedEntry.key]?.selectedIds ?? []}
      library={library}
      refreshing={refreshing}
      notice={collectionNotice || (parts[selectedEntry.key]?.fallbackSource ? "目录未包含当前视频。返回后仍只处理粘贴的视频；手动选择后使用选中项。" : "")}
      onChange={(ids) => setParts((current) => ({ ...current, [selectedEntry.key]: { selectedIds: ids } }))}
      onRefresh={() => void refreshCollection(selectedEntry)}
      onBack={() => setActiveCollection(null)}
      onOpen={openArchive}
    />
  }

  return (
    <div className="flex min-h-0 flex-col gap-3">
      <div className={cn("space-y-3", mode === "dialog" && "min-h-0 overflow-y-auto pr-1")}>
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
          className="max-h-48 min-h-20 resize-none text-base"
        />
        <p className="text-sm leading-relaxed text-muted-foreground">
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
            const skipped = Boolean(state)
            const collection = details?.collection
            const multi = isMultiPart(collection)
            const selection = parts[entry.key]
            return (
              <li key={entry.key} className={cn("px-3 py-2 text-base", skipped && "bg-muted/40")}>
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
                    <p className="flex min-w-0 flex-wrap items-center gap-x-1.5 text-sm text-muted-foreground">
                      {info === "loading" && <span className="inline-flex items-center gap-1"><HugeiconsIcon icon={Loading03Icon} className="size-3 animate-spin" />读取信息…</span>}
                      {entry.kind === "path" && <span>本机文件</span>}
                      {details?.uploader && <span className="max-w-48 truncate">{details.uploader}</span>}
                      {details?.duration ? <span className="tabular-nums">{formatDuration(details.duration)}</span> : null}
                      {multi && <span>{collection.collection_type === "ugc_season" ? "合集" : "分 P"} · {collection.items.length} 集</span>}
                      {details?.subtitles?.length ? <span title={details.subtitles.join(", ")}>有平台字幕</span>
                        : details?.autoCaptions ? <span>有自动字幕</span> : null}
                    </p>
                    {state && (
                      <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-sm">
                        <span className={state.kind === "failed" ? "text-muted-foreground" : "text-amber-700 dark:text-amber-400"}>
                          {state.kind === "completed" ? "已在库中"
                            : state.kind === "processing" ? "正在处理"
                              : state.kind === "paused" ? "已暂停，在活动里可以继续"
                                : state.match.status === "cancelled" ? "已取消，可打开任务选择后续操作" : "之前失败，可打开任务选择后续操作"}
                        </span>
                        <button type="button" className="text-primary hover:underline" onClick={() => openArchive(state.match)}>打开</button>
                        {state.kind !== "processing" && state.kind !== "paused" && (
                          <button type="button" disabled={submitting} className="text-muted-foreground hover:text-foreground hover:underline" onClick={() => void rerun(entry, state.match)}>仍然重新处理</button>
                        )}
                      </p>
                    )}
                    {entry.kind === "url" && library[entry.source] == null && <p className="mt-1 text-sm text-muted-foreground">状态未确认</p>}
                    {entry.platform === "bilibili" && info === null && <button type="button" className="mt-1 text-sm text-primary" onClick={() => {
                      metaRequested.current.delete(entry.key)
                      void fetchSourceMeta(entry, true).then((value) => {
                        setMeta((current) => ({ ...current, [entry.key]: value }))
                        if (value?.collection?.is_collection) {
                          const currentId = value.collection.current_item_id
                          const present = value.collection.items.some((item) => item.id === currentId)
                          setParts((current) => ({ ...current, [entry.key]: current[entry.key] ?? { selectedIds: present && currentId ? [currentId] : [], fallbackSource: !present } }))
                        }
                      })
                    }}>重新读取目录</button>}
                    {multi && selection && (
                      <div className="mt-1">
                        <button
                          type="button"
                          className="text-sm text-primary hover:underline"
                          disabled={submitting}
                          onClick={() => { setActiveCollection(entry.key); setCollectionNotice(""); void lookup(collection.items.map((item) => item.url)) }}
                        >
                          已选 {selection.fallbackSource ? 1 : selection.selectedIds.length} 项 · 选择其他视频
                        </button>
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
            <li key={item.id} className={cn("flex items-center gap-2.5 px-3 py-2 text-base", item.error && "bg-destructive/10 text-destructive")}>
              <HugeiconsIcon
                icon={item.uploading ? Loading03Icon : isVideoName(item.name) ? FileVideoIcon : FileAudioIcon}
                className={cn("size-4 shrink-0 text-muted-foreground", item.uploading && "animate-spin")}
              />
              <span className="min-w-0 flex-1 truncate font-medium">{item.name}</span>
              <span className="shrink-0 text-sm tabular-nums text-muted-foreground">
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
        <p className="text-sm text-muted-foreground">有 {unrecognized.length} 行没有找到链接，会被忽略。</p>
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
      <details className="rounded-lg border p-3" open={workflowError || undefined}>
        <summary className="cursor-pointer text-sm font-medium">
          处理流程 · {workflowOverride ? "本次自定义" : "使用全局默认"}
          <span className="ml-2 font-normal text-muted-foreground">ASR 必选 · {workflow.separate ? "分离人声" : "跳过人声分离"}</span>
        </summary>
        <div className="mt-4 flex flex-col gap-3">
          {workflowError && <p className="text-sm text-muted-foreground">未能读取全局默认，将按下方已选步骤提交。</p>}
          <WorkflowControls value={workflow} onChange={setWorkflowOverride} disabled={submitting || !workflowLoaded} />
          {workflowOverride && <Button type="button" variant="ghost" size="sm" className="self-start"
            onClick={() => setWorkflowOverride(null)}>恢复全局默认</Button>}
          <p className="text-xs text-muted-foreground">这里的调整只影响本次提交{total > 1 ? `的 ${total} 项` : ""}。</p>
        </div>
      </details>
      <fieldset className="grid gap-x-4 gap-y-2 rounded-lg border bg-muted/30 px-3 py-2.5 sm:grid-cols-[auto_auto_minmax(0,1fr)]">
        <legend className="sr-only">处理选项</legend>
        <label className="flex flex-col gap-1">
          <span className="text-sm font-medium">说话人数</span>
          <Input
            type="number"
            min="1"
            max="20"
            value={numSpeakers}
            onChange={(event) => setNumSpeakers(event.target.value)}
            placeholder="自动"
            className="h-8 w-20 text-sm"
          />
        </label>
        <div className="flex min-w-0 flex-col gap-1">
          <span className="text-sm font-medium">热词</span>
          <div
            className="flex min-h-8 cursor-text flex-wrap items-center gap-1 rounded-md border bg-background px-1.5 py-1"
            onClick={() => hotwordInputRef.current?.focus()}
          >
            {hotwords.map((tag) => (
              <span key={tag} className="inline-flex items-center gap-0.5 rounded bg-primary/10 px-1.5 py-0.5 text-sm font-medium text-primary">
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
              className="min-w-24 flex-1 bg-transparent py-0.5 text-sm outline-none placeholder:text-muted-foreground/70"
            />
          </div>
        </div>
      </fieldset>

      {error && <p className="text-sm text-destructive" role="alert">{error}</p>}
      {summary && <div className="space-y-2 rounded-lg border p-3 text-sm" role="status">
        {Boolean(summary.folderWarnings) && <p>{summary.folderWarnings} 项未改变文件夹归属。已有归属会保留；命中多个合集时需手动选择。</p>}
        <p>新增 {summary.created} 项 · 复用 {summary.reused} 项 · 处理中 {summary.active} 项 · 待操作 {summary.needsAction} 项 · 失败 {summary.failed} 项</p>
        {summary.failed > 0 && <p>再次提交只重试失败或结果未确认的项目。上传文件和已成功结果已保留。</p>}
        <div className="max-h-40 space-y-2 overflow-y-auto">
        {Object.values(results).filter((item) => item.outcome === "failed" || item.outcome === "skipped" || item.folder_error || ["kept_other", "ambiguous"].includes(item.folder_outcome ?? "")).map((item) => <div key={item.source} className="flex min-w-0 items-center gap-2">
          <span className="min-w-0 flex-1 truncate">{item.match?.title || sourceLabel(item.source)}{item.error || item.folder_error ? `：${item.error || item.folder_error}` : item.folder_outcome === "kept_other" ? "：保留已有文件夹" : item.folder_outcome === "ambiguous" ? "：命中多个合集" : ""}</span>
          {item.match && <button type="button" className="shrink-0 text-primary" onClick={() => openArchive(item.match!)}>打开</button>}
          {!item.match && item.task && <button type="button" className="shrink-0 text-primary" onClick={() => { onNavigate?.(); navigate(`#/result/task/${item.task!.id}`) }}>打开任务</button>}
        </div>)}
        </div>
        {Object.values(results).some((item) => item.outcome === "created" && item.task) && <button type="button" className="text-primary" onClick={() => {
          const item = Object.values(results).find((result) => result.outcome === "created" && result.task)
          if (item?.task) { onNavigate?.(); navigate(`#/result/task/${item.task.id}`) }
        }}>查看已创建任务</button>}
      </div>}
      {!online && <p className="text-sm text-muted-foreground">连接服务器后才能提交。</p>}
      </div>

      <div className="flex shrink-0 flex-wrap items-center gap-2">
        {urlEntries.map((entry) => {
          const information = meta[entry.key]
          if (!information || information === "loading" || !isMultiPart(information.collection)) return null
          return <label key={entry.key} className="flex w-full items-center gap-2 text-sm"><input type="checkbox" checked={collectionFolders.has(entry.source)} disabled={submitting} onChange={(event) => setCollectionFolders((current) => { const next = new Set(current); if (event.target.checked) next.add(entry.source); else next.delete(entry.source); return next })} />以「{information.collection.title || information.title || "此合集"}」创建文件夹</label>
        })}
        <label className="flex items-center gap-2 text-sm">导入到<select className="h-9 max-w-52 rounded-md border bg-background px-2" aria-label="导入到文件夹" value={destination} disabled={submitting} onChange={(event) => setDestination(event.target.value)}><option value="auto">自动归入已识别合集</option><option value="none">未归类</option>{folders.map((folder) => <option key={folder.id} value={folder.id}>{folder.name}</option>)}</select></label>
        <label className="flex items-center gap-1.5 text-xs"><input type="checkbox" checked={moveExisting} disabled={submitting} onChange={(event) => setMoveExisting(event.target.checked)} />移动已有归属的内容</label>
        <p className="min-w-0 flex-1 text-sm text-muted-foreground">
          {uploadingCount > 0 ? `${uploadingCount} 个文件上传中…`
            : total > 100 ? `已选 ${total} 项，将分 ${Math.ceil(total / 100)} 批提交，每批最多 100 项`
              : total > 0 ? `预计新增 ${preview.fresh} · 复用 ${preview.reused} · 处理中 ${preview.active}${preview.needsAction ? ` · 待操作 ${preview.needsAction}` : ""}${preview.unknown ? ` · 状态未确认 ${preview.unknown}` : ""}`
              : mode === "dialog" ? "Ctrl+Enter 开始处理" : ""}
        </p>
        {mode === "dialog" && (
          <Button type="button" variant="ghost" onClick={onCancel} disabled={submitting}>取消</Button>
        )}
        <Button type="button" disabled={!canSubmit} onClick={() => void handleSubmit()} className="min-w-28 text-base">
          <HugeiconsIcon icon={submitting ? Loading03Icon : PlayIcon} className={cn("size-4", submitting && "animate-spin")} />
          {submitting ? "提交中…" : total > 1 ? `开始处理（${total} 个）` : "开始处理"}
        </Button>
      </div>
    </div>
  )
}
