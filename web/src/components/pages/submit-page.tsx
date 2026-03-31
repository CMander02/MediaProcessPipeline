import { useState, useRef, useCallback, type FormEvent, type KeyboardEvent } from "react"
import { useDropZone } from "@/hooks/use-drop-zone"
import { navigate } from "@/lib/router"
import { api } from "@/lib/api"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import { Label } from "@/components/ui/label"
import { FolderQueueDialog } from "@/components/folder-queue-dialog"
import { HugeiconsIcon } from "@hugeicons/react"
import {
  Upload01Icon, Link01Icon, Loading03Icon, PlayIcon, ArrowDown01Icon, ArrowUp01Icon,
  FileVideoIcon, FileAudioIcon, FolderOpenIcon, Folder01Icon, Cancel01Icon, CheckmarkCircle02Icon,
} from "@hugeicons/core-free-icons"
import { cn } from "@/lib/utils"
import { formatDuration } from "@/lib/format"

interface QueuedFile {
  id: string
  name: string
  size: number
  duration: number | null
  taskId: string
  outputDir: string
  uploading: boolean
  error: string
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

function getMediaDuration(file: File): Promise<number | null> {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file)
    const el = file.type.startsWith("video/") ? document.createElement("video") : document.createElement("audio")
    el.preload = "metadata"
    el.onloadedmetadata = () => { URL.revokeObjectURL(url); resolve(isFinite(el.duration) ? el.duration : null) }
    el.onerror = () => { URL.revokeObjectURL(url); resolve(null) }
    el.src = url
  })
}

function isVideoFile(name: string) {
  return /\.(mp4|mkv|avi|webm|mov|flv|wmv)$/i.test(name)
}

export function SubmitPage() {
  const [source, setSource] = useState("")
  const [queuedFiles, setQueuedFiles] = useState<QueuedFile[]>([])
  const [forceAsr, setForceAsr] = useState(false)
  const [numSpeakers, setNumSpeakers] = useState("")
  const [hotwordTags, setHotwordTags] = useState<string[]>([])
  const [hotwordInput, setHotwordInput] = useState("")
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState("")
  const [showFolderDialog, setShowFolderDialog] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const hotwordInputRef = useRef<HTMLInputElement>(null)
  const abortControllers = useRef<Map<string, AbortController>>(new Map())

  const buildOptions = () => {
    const opts: Record<string, unknown> = {}
    if (forceAsr) opts.force_asr = true
    const ns = parseInt(numSpeakers, 10)
    if (ns > 0) opts.num_speakers = ns
    if (hotwordTags.length > 0) opts.hotwords = hotwordTags
    return opts
  }
  const buildOptionsRef = useRef(buildOptions)
  buildOptionsRef.current = buildOptions

  const uploadAndQueue = useCallback(async (file: File) => {
    const id = `${file.name}-${Date.now()}-${Math.random()}`
    const controller = new AbortController()
    abortControllers.current.set(id, controller)
    setQueuedFiles((prev) => [...prev, {
      id, name: file.name, size: file.size, duration: null,
      taskId: "", outputDir: "", uploading: true, error: "",
    }])
    try {
      const [duration, task] = await Promise.all([
        getMediaDuration(file),
        api.pipeline.upload(file, buildOptionsRef.current(), controller.signal),
      ])
      const outputDir = (task.result as Record<string, unknown> | null)?.output_dir as string || ""
      setQueuedFiles((prev) => prev.map((f) =>
        f.id === id ? { ...f, duration, taskId: task.id, outputDir, uploading: false } : f
      ))
    } catch (err) {
      if (controller.signal.aborted) {
        setQueuedFiles((prev) => prev.filter((f) => f.id !== id))
      } else {
        setQueuedFiles((prev) => prev.map((f) =>
          f.id === id ? { ...f, uploading: false, error: "上传失败" } : f
        ))
      }
    } finally {
      abortControllers.current.delete(id)
    }
  }, [])

  const handleFileSelect = useCallback((files: File[]) => {
    for (const f of files) uploadAndQueue(f)
  }, [uploadAndQueue])

  const removeQueued = (id: string) => {
    abortControllers.current.get(id)?.abort()
    abortControllers.current.delete(id)
    setQueuedFiles((prev) => prev.filter((f) => f.id !== id))
  }
  const clearAll = () => {
    for (const controller of abortControllers.current.values()) controller.abort()
    abortControllers.current.clear()
    setQueuedFiles([])
  }

  const handleSubmitAll = async () => {
    // Uploaded files already have tasks — only URL/path sources need creation
    const readyFiles = queuedFiles.filter((f) => f.taskId && !f.uploading && !f.error)
    const urlSource = source.trim()
    if (!urlSource && !readyFiles.length) return
    if (submitting) return

    setSubmitting(true)
    setError("")
    try {
      const opts = buildOptions()

      // Single uploaded file, no URL
      if (!urlSource && readyFiles.length === 1) {
        const f = readyFiles[0]
        if (f.outputDir) {
          navigate(`#/result/archive?path=${encodeURIComponent(f.outputDir)}&taskId=${encodeURIComponent(f.taskId)}`)
        } else {
          navigate(`#/result/task/${f.taskId}`)
        }
        return
      }

      // Single URL, no files
      if (urlSource && !readyFiles.length) {
        const task = await api.tasks.create(urlSource, opts)
        const outputDir = task.result?.output_dir as string | undefined
        if (outputDir) {
          navigate(`#/result/archive?path=${encodeURIComponent(outputDir)}&taskId=${encodeURIComponent(task.id)}`)
        } else {
          navigate(`#/result/task/${task.id}`)
        }
        return
      }

      // Multiple items — create tasks for URL sources (file tasks already exist)
      if (urlSource) await api.tasks.create(urlSource, opts)
      navigate("#/files")
    } catch (err) {
      setError(err instanceof Error ? err.message : "提交失败")
      setSubmitting(false)
    }
  }

  const handleURLSubmit = (e: FormEvent) => { e.preventDefault(); handleSubmitAll() }

  const addHotword = useCallback((word: string) => {
    const w = word.trim()
    if (w && !hotwordTags.includes(w)) setHotwordTags((prev) => [...prev, w])
  }, [hotwordTags])

  const removeHotword = useCallback((word: string) => {
    setHotwordTags((prev) => prev.filter((t) => t !== word))
  }, [])

  const handleHotwordKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || e.key === "," || e.key === "、") {
      e.preventDefault()
      const val = hotwordInput.replace(/[,，、]/g, "").trim()
      if (val) { addHotword(val); setHotwordInput("") }
    } else if (e.key === "Backspace" && !hotwordInput && hotwordTags.length > 0) {
      setHotwordTags((prev) => prev.slice(0, -1))
    }
  }

  const { isDragging, dropZoneProps } = useDropZone({ accept: ["video/*", "audio/*"], onDrop: handleFileSelect })

  const anyUploading = queuedFiles.some((f) => f.uploading)
  const readyCount = queuedFiles.filter((f) => f.taskId && !f.error).length
  const uploadingCount = queuedFiles.filter((f) => f.uploading).length
  const totalCount = (source.trim() ? 1 : 0) + readyCount
  const canSubmit = totalCount > 0 && !submitting && !anyUploading
  const hasFiles = queuedFiles.length > 0
  const activeOptions = [forceAsr, !!numSpeakers, hotwordTags.length > 0].filter(Boolean).length

  return (
    <div className="flex h-full overflow-hidden">
      <FolderQueueDialog
        open={showFolderDialog}
        onOpenChange={setShowFolderDialog}
        options={buildOptions()}
        onSubmitted={() => navigate("#/files")}
      />

      {/* ── Left panel: controls ── */}
      <div className={cn(
        "flex flex-col gap-4 p-6 overflow-y-auto shrink-0 transition-all duration-200",
        hasFiles ? "w-72 border-r" : "w-full items-center justify-center",
      )}>
        <div className={cn("flex flex-col gap-4", !hasFiles && "w-full max-w-md")}>

          {/* Drop zone */}
          <div
            {...dropZoneProps}
            className={cn(
              "flex flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed transition-colors cursor-pointer",
              hasFiles ? "py-5 px-4" : "py-12 px-4",
              isDragging ? "border-primary bg-primary/5" : "border-muted-foreground/25 hover:border-muted-foreground/40 hover:bg-muted/20",
              submitting && "pointer-events-none opacity-60",
            )}
            onClick={() => fileInputRef.current?.click()}
          >
            <HugeiconsIcon icon={Upload01Icon} className={cn("text-muted-foreground/40", hasFiles ? "h-6 w-6" : "h-9 w-9")} />
            <div className="text-center pointer-events-none">
              <p className="text-sm font-medium text-muted-foreground">
                {isDragging ? "松开鼠标放下文件" : hasFiles ? "继续拖入或点击添加" : "拖放音视频文件到这里"}
              </p>
              {!hasFiles && (
                <p className="mt-0.5 text-xs text-muted-foreground/60">支持 MP4、MKV、MP3、WAV、FLAC 等，可多选</p>
              )}
            </div>
            <div className="flex items-center gap-2 pointer-events-auto" onClick={(e) => e.stopPropagation()}>
              <Button variant="outline" size="sm" onClick={() => fileInputRef.current?.click()}>
                <HugeiconsIcon icon={Upload01Icon} className="h-3.5 w-3.5 mr-1.5" />
                选择文件
              </Button>
              <Button variant="outline" size="sm" onClick={() => setShowFolderDialog(true)}>
                <HugeiconsIcon icon={FolderOpenIcon} className="h-3.5 w-3.5 mr-1.5" />
                选择文件夹
              </Button>
            </div>
            <input
              ref={fileInputRef}
              type="file"
              accept="video/*,audio/*"
              multiple
              className="hidden"
              onChange={(e) => {
                if (e.target.files) { handleFileSelect(Array.from(e.target.files)); e.target.value = "" }
              }}
            />
          </div>

          {/* Divider */}
          <div className="flex items-center gap-3">
            <div className="h-px flex-1 bg-border" />
            <span className="text-xs text-muted-foreground">或输入链接</span>
            <div className="h-px flex-1 bg-border" />
          </div>

          {/* URL input */}
          <form onSubmit={handleURLSubmit} className="flex gap-2">
            <div className="relative flex-1">
              <HugeiconsIcon icon={Link01Icon} className="absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground pointer-events-none" />
              <Input
                value={source}
                onChange={(e) => setSource(e.target.value)}
                placeholder="粘贴视频链接或本地路径..."
                className="pl-9"
                disabled={submitting}
                autoComplete="off"
              />
            </div>
          </form>

          {/* Submit */}
          <Button size="lg" disabled={!canSubmit} onClick={handleSubmitAll} className="w-full">
            {submitting
              ? <HugeiconsIcon icon={Loading03Icon} className="h-4 w-4 animate-spin mr-2" />
              : <HugeiconsIcon icon={PlayIcon} className="h-4 w-4 mr-2" />
            }
            {submitting
              ? "提交中..."
              : totalCount > 1
                ? `开始处理（${totalCount} 个）`
                : "开始处理"
            }
          </Button>

          {error && <p className="text-sm text-destructive text-center">{error}</p>}

          {/* Advanced options */}
          <div>
            <button
              type="button"
              className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
              onClick={() => setShowAdvanced(!showAdvanced)}
            >
              {showAdvanced ? <HugeiconsIcon icon={ArrowUp01Icon} className="h-3.5 w-3.5" /> : <HugeiconsIcon icon={ArrowDown01Icon} className="h-3.5 w-3.5" />}
              高级选项
              {activeOptions > 0 && (
                <span className="ml-0.5 inline-flex h-4 w-4 items-center justify-center rounded-full bg-primary text-[10px] text-primary-foreground font-medium">
                  {activeOptions}
                </span>
              )}
            </button>

            {showAdvanced && (
              <div className="mt-3 space-y-4 rounded-lg border p-4 bg-muted/30">
                <div className="flex items-center gap-2">
                  <Switch id="force-asr" checked={forceAsr} onCheckedChange={setForceAsr} />
                  <Label htmlFor="force-asr" className="text-sm cursor-pointer">强制语音识别</Label>
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="num-speakers" className="text-sm">说话人数量</Label>
                  <Input
                    id="num-speakers"
                    type="number" min="1" max="20"
                    value={numSpeakers}
                    onChange={(e) => setNumSpeakers(e.target.value)}
                    placeholder="留空自动检测"
                    className="max-w-36 h-8"
                  />
                </div>

                <div className="space-y-1.5">
                  <Label className="text-sm">热词</Label>
                  <div
                    className="flex flex-wrap gap-1.5 min-h-[2.25rem] rounded-md border bg-background px-2 py-1.5 cursor-text"
                    onClick={() => hotwordInputRef.current?.focus()}
                  >
                    {hotwordTags.map((tag) => (
                      <span key={tag} className="inline-flex items-center gap-0.5 rounded-md bg-primary/10 text-primary px-2 py-0.5 text-xs font-medium">
                        {tag}
                        <button type="button" onClick={(e) => { e.stopPropagation(); removeHotword(tag) }} className="ml-0.5 rounded-full hover:bg-primary/20 p-0.5">
                          <HugeiconsIcon icon={Cancel01Icon} className="h-2.5 w-2.5" />
                        </button>
                      </span>
                    ))}
                    <input
                      ref={hotwordInputRef}
                      value={hotwordInput}
                      onChange={(e) => setHotwordInput(e.target.value)}
                      onKeyDown={handleHotwordKeyDown}
                      onBlur={() => { const v = hotwordInput.replace(/[,，、]/g, "").trim(); if (v) { addHotword(v); setHotwordInput("") } }}
                      placeholder={hotwordTags.length === 0 ? "按回车添加..." : ""}
                      className="flex-1 min-w-[6rem] bg-transparent text-xs outline-none placeholder:text-muted-foreground/60 py-0.5"
                    />
                  </div>
                </div>

                {totalCount > 1 && (
                  <p className="text-xs text-amber-600 dark:text-amber-400">以上选项将应用于全部 {totalCount} 个文件</p>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ── Right panel: file list ── */}
      {hasFiles && (
        <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
          {/* Header */}
          <div className="shrink-0 flex items-center justify-between px-4 py-3 border-b">
            <div className="flex items-center gap-2 text-sm">
              <span className="font-medium">{queuedFiles.length} 个文件</span>
              {uploadingCount > 0 && (
                <span className="text-muted-foreground flex items-center gap-1">
                  <HugeiconsIcon icon={Loading03Icon} className="h-3.5 w-3.5 animate-spin" />
                  {uploadingCount} 个上传中
                </span>
              )}
              {uploadingCount === 0 && readyCount > 0 && (
                <span className="text-emerald-600 flex items-center gap-1">
                  <HugeiconsIcon icon={CheckmarkCircle02Icon} className="h-3.5 w-3.5" />
                  全部就绪
                </span>
              )}
            </div>
            <button
              className="text-xs text-muted-foreground hover:text-destructive transition-colors"
              onClick={clearAll}
            >
              清空列表
            </button>
          </div>

          {/* Scrollable list */}
          <div className="flex-1 overflow-y-auto px-4 py-2">
            <div className="space-y-1">
              {queuedFiles.map((f) => (
                <div
                  key={f.id}
                  className={cn(
                    "flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm group",
                    f.error ? "bg-destructive/10 text-destructive" : "hover:bg-muted/60",
                  )}
                >
                  {f.uploading ? (
                    <HugeiconsIcon icon={Loading03Icon} className="h-4 w-4 animate-spin text-muted-foreground shrink-0" />
                  ) : f.error ? (
                    <HugeiconsIcon icon={Cancel01Icon} className="h-4 w-4 shrink-0" />
                  ) : isVideoFile(f.name) ? (
                    <HugeiconsIcon icon={FileVideoIcon} className="h-4 w-4 text-muted-foreground shrink-0" />
                  ) : (
                    <HugeiconsIcon icon={FileAudioIcon} className="h-4 w-4 text-muted-foreground shrink-0" />
                  )}

                  <span className="flex-1 truncate">{f.name}</span>

                  <span className="text-xs text-muted-foreground shrink-0 tabular-nums">
                    {f.error ? f.error : f.uploading ? "上传中..." : (
                      <>
                        {formatFileSize(f.size)}
                        {f.duration != null && <span className="ml-1.5">{formatDuration(f.duration)}</span>}
                      </>
                    )}
                  </span>

                  <button
                    onClick={() => removeQueued(f.id)}
                    className="p-0.5 rounded text-muted-foreground/40 hover:text-muted-foreground opacity-0 group-hover:opacity-100 transition-all shrink-0"
                  >
                    <HugeiconsIcon icon={Cancel01Icon} className="h-3.5 w-3.5" />
                  </button>
                </div>
              ))}
            </div>
          </div>

          {/* Footer hint */}
          <div className="shrink-0 px-4 py-2 border-t">
            <button
              className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
              onClick={() => setShowFolderDialog(true)}
            >
              <HugeiconsIcon icon={Folder01Icon} className="h-3.5 w-3.5" />
              从文件夹继续添加
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
