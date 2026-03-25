import { useState, useRef, type FormEvent } from "react"
import { useDropZone } from "@/hooks/use-drop-zone"
import { navigate } from "@/lib/router"
import { api } from "@/lib/api"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import { Label } from "@/components/ui/label"
import { Upload, Link, Loader2, Play, ChevronDown, ChevronUp, FileVideo, FileAudio, X } from "lucide-react"
import { cn } from "@/lib/utils"
import { formatDuration } from "@/lib/format"

interface SelectedFile {
  name: string
  size: number
  duration: number | null
  serverPath: string // path returned by upload API
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

function getMediaDuration(file: File): Promise<number | null> {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file)
    const el = file.type.startsWith("video/")
      ? document.createElement("video")
      : document.createElement("audio")
    el.preload = "metadata"
    el.onloadedmetadata = () => {
      const dur = isFinite(el.duration) ? el.duration : null
      URL.revokeObjectURL(url)
      resolve(dur)
    }
    el.onerror = () => {
      URL.revokeObjectURL(url)
      resolve(null)
    }
    el.src = url
  })
}

export function SubmitPage() {
  const [source, setSource] = useState("")
  const [selectedFile, setSelectedFile] = useState<SelectedFile | null>(null)
  const [uploading, setUploading] = useState(false)
  const [skipSep, setSkipSep] = useState(false)
  const [numSpeakers, setNumSpeakers] = useState("")
  const [hotwords, setHotwords] = useState("")
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState("")
  const fileInputRef = useRef<HTMLInputElement>(null)

  const buildOptions = () => {
    const opts: Record<string, unknown> = {}
    if (skipSep) opts.skip_separation = true
    const ns = parseInt(numSpeakers, 10)
    if (ns > 0) opts.num_speakers = ns
    const hw = hotwords
      .split(/[,，、\n]+/)
      .map((s) => s.trim())
      .filter(Boolean)
    if (hw.length > 0) opts.hotwords = hw
    return opts
  }

  const navigateToResult = (task: { id: string; result?: Record<string, unknown> | null }) => {
    const outputDir = task.result?.output_dir as string | undefined
    if (outputDir) {
      navigate(`#/result/archive?path=${encodeURIComponent(outputDir)}&taskId=${encodeURIComponent(task.id)}`)
    } else {
      navigate(`#/result/task/${task.id}`)
    }
  }

  const handleFileSelect = async (files: File[]) => {
    if (files.length === 0 || uploading) return
    const file = files[0]
    setUploading(true)
    setError("")
    try {
      const [duration, { file_path }] = await Promise.all([
        getMediaDuration(file),
        api.pipeline.upload(file),
      ])
      setSelectedFile({
        name: file.name,
        size: file.size,
        duration,
        serverPath: file_path,
      })
      setSource("") // clear URL input when file is selected
    } catch (err) {
      setError(err instanceof Error ? err.message : "上传失败")
    } finally {
      setUploading(false)
    }
  }

  const clearFile = () => {
    setSelectedFile(null)
    if (fileInputRef.current) fileInputRef.current.value = ""
  }

  const handleSubmit = async () => {
    const src = selectedFile ? selectedFile.serverPath : source.trim()
    if (!src || submitting) return
    setSubmitting(true)
    setError("")
    try {
      const task = await api.tasks.create(src, buildOptions())
      navigateToResult(task)
    } catch (err) {
      setError(err instanceof Error ? err.message : "提交失败")
      setSubmitting(false)
    }
  }

  const handleURLSubmit = (e: FormEvent) => {
    e.preventDefault()
    handleSubmit()
  }

  const isVideo = selectedFile?.name.match(/\.(mp4|mkv|avi|webm|mov)$/i)

  const { isDragging, dropZoneProps } = useDropZone({
    accept: ["video/*", "audio/*"],
    onDrop: handleFileSelect,
  })

  const canSubmit = !!(selectedFile || source.trim()) && !submitting && !uploading

  return (
    <div className="flex h-full items-center justify-center p-6">
      <div className="w-full max-w-xl space-y-6">
        {/* Drop zone */}
        <div
          {...dropZoneProps}
          className={cn(
            "relative flex flex-col items-center justify-center gap-4 rounded-xl border-2 border-dashed p-10 transition-colors",
            isDragging
              ? "border-primary bg-primary/5"
              : selectedFile
                ? "border-primary/40 bg-primary/5"
                : "border-muted-foreground/25 hover:border-muted-foreground/50",
            (submitting || uploading) && "pointer-events-none opacity-60",
          )}
        >
          {uploading ? (
            <>
              <Loader2 className="h-8 w-8 animate-spin text-primary" />
              <p className="text-sm text-muted-foreground">上传中...</p>
            </>
          ) : selectedFile ? (
            <>
              {/* File preview */}
              <div className="flex items-center gap-3 w-full">
                {isVideo ? (
                  <FileVideo className="h-10 w-10 text-primary shrink-0" />
                ) : (
                  <FileAudio className="h-10 w-10 text-primary shrink-0" />
                )}
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium truncate">{selectedFile.name}</p>
                  <p className="text-xs text-muted-foreground">
                    {formatFileSize(selectedFile.size)}
                    {selectedFile.duration != null && (
                      <span className="ml-2">{formatDuration(selectedFile.duration)}</span>
                    )}
                  </p>
                </div>
                <button
                  onClick={(e) => { e.stopPropagation(); clearFile() }}
                  className="p-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                  title="移除文件"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
            </>
          ) : (
            <>
              <Upload className="h-10 w-10 text-muted-foreground/50" />
              <div className="text-center">
                <p className="text-sm font-medium">
                  {isDragging ? "松开鼠标放下文件" : "拖放音视频文件到这里"}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  支持 MP4、MKV、MP3、WAV、FLAC 等格式
                </p>
              </div>
              <Button
                variant="outline"
                size="sm"
                onClick={() => fileInputRef.current?.click()}
              >
                选择文件
              </Button>
            </>
          )}
          <input
            ref={fileInputRef}
            type="file"
            accept="video/*,audio/*"
            className="hidden"
            onChange={(e) => {
              if (e.target.files?.[0]) handleFileSelect([e.target.files[0]])
            }}
          />
        </div>

        {/* Divider */}
        {!selectedFile && (
          <>
            <div className="flex items-center gap-3">
              <div className="h-px flex-1 bg-border" />
              <span className="text-xs text-muted-foreground">或</span>
              <div className="h-px flex-1 bg-border" />
            </div>

            {/* URL input */}
            <form onSubmit={handleURLSubmit} className="flex gap-2">
              <div className="relative flex-1">
                <Link className="absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={source}
                  onChange={(e) => setSource(e.target.value)}
                  placeholder="粘贴视频链接或本地文件路径..."
                  className="pl-9"
                  disabled={submitting}
                  autoComplete="off"
                />
              </div>
              <Button type="submit" disabled={!canSubmit}>
                {submitting ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Play className="h-4 w-4" />
                )}
                <span className="ml-1.5">处理</span>
              </Button>
            </form>
          </>
        )}

        {/* Submit button for file mode */}
        {selectedFile && (
          <Button
            className="w-full"
            size="lg"
            disabled={!canSubmit}
            onClick={handleSubmit}
          >
            {submitting ? (
              <Loader2 className="h-4 w-4 animate-spin mr-2" />
            ) : (
              <Play className="h-4 w-4 mr-2" />
            )}
            开始处理
          </Button>
        )}

        {/* Advanced options toggle */}
        <div className="flex items-center justify-between">
          <button
            type="button"
            className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
            onClick={() => setShowAdvanced(!showAdvanced)}
          >
            {showAdvanced ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
            高级选项
          </button>
          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>

        {/* Advanced options panel */}
        {showAdvanced && (
          <div className="space-y-4 rounded-lg border p-4 bg-muted/30">
            <div className="flex items-center gap-2">
              <Switch id="skip-sep" checked={skipSep} onCheckedChange={setSkipSep} />
              <Label htmlFor="skip-sep" className="text-sm">
                跳过人声分离
              </Label>
              <span className="text-xs text-muted-foreground ml-1">（纯人声内容无需分离）</span>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <Label htmlFor="num-speakers" className="text-sm">
                  说话人数量
                </Label>
                <Input
                  id="num-speakers"
                  type="number"
                  min="1"
                  max="20"
                  value={numSpeakers}
                  onChange={(e) => setNumSpeakers(e.target.value)}
                  placeholder="留空自动检测"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="hotwords" className="text-sm">
                  热词
                </Label>
                <Input
                  id="hotwords"
                  value={hotwords}
                  onChange={(e) => setHotwords(e.target.value)}
                  placeholder="逗号分隔，如 DeepSeek, GPT-4"
                />
                <p className="text-xs text-muted-foreground">用于 LLM 润色时修正专有名词</p>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
