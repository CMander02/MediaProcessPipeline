import { useState, useRef, type FormEvent } from "react"
import { useDropZone } from "@/hooks/use-drop-zone"
import { navigate } from "@/lib/router"
import { api } from "@/lib/api"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import { Label } from "@/components/ui/label"
import { Upload, Link, Loader2, Play } from "lucide-react"
import { cn } from "@/lib/utils"

export function SubmitPage() {
  const [source, setSource] = useState("")
  const [skipSep, setSkipSep] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState("")
  const fileInputRef = useRef<HTMLInputElement>(null)

  const submitSource = async (src: string) => {
    setSubmitting(true)
    setError("")
    try {
      const opts = skipSep ? { skip_separation: true } : {}
      const task = await api.tasks.create(src, opts)
      navigate(`#/result/task/${task.id}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : "提交失败")
      setSubmitting(false)
    }
  }

  const handleFileDrop = async (files: File[]) => {
    if (files.length === 0 || submitting) return
    setSubmitting(true)
    setError("")
    try {
      const { file_path } = await api.pipeline.upload(files[0])
      const opts = skipSep ? { skip_separation: true } : {}
      const task = await api.tasks.create(file_path, opts)
      navigate(`#/result/task/${task.id}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : "上传失败")
      setSubmitting(false)
    }
  }

  const handleURLSubmit = (e: FormEvent) => {
    e.preventDefault()
    const val = source.trim()
    if (!val || submitting) return
    submitSource(val)
  }

  const { isDragging, dropZoneProps } = useDropZone({
    accept: ["video/*", "audio/*"],
    onDrop: handleFileDrop,
  })

  return (
    <div className="flex h-full items-center justify-center p-6">
      <div className="w-full max-w-xl space-y-6">
        {/* Drop zone */}
        <div
          {...dropZoneProps}
          className={cn(
            "relative flex flex-col items-center justify-center gap-4 rounded-xl border-2 border-dashed p-12 transition-colors",
            isDragging
              ? "border-primary bg-primary/5"
              : "border-muted-foreground/25 hover:border-muted-foreground/50",
            submitting && "pointer-events-none opacity-60",
          )}
        >
          {submitting ? (
            <Loader2 className="h-10 w-10 animate-spin text-primary" />
          ) : (
            <Upload className="h-10 w-10 text-muted-foreground/50" />
          )}
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
            disabled={submitting}
            onClick={() => fileInputRef.current?.click()}
          >
            选择文件
          </Button>
          <input
            ref={fileInputRef}
            type="file"
            accept="video/*,audio/*"
            className="hidden"
            onChange={(e) => {
              if (e.target.files?.[0]) handleFileDrop([e.target.files[0]])
            }}
          />
        </div>

        {/* Divider */}
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
          <Button type="submit" disabled={!source.trim() || submitting}>
            {submitting ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Play className="h-4 w-4" />
            )}
            <span className="ml-1.5">处理</span>
          </Button>
        </form>

        {/* Options */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Switch id="skip-sep" checked={skipSep} onCheckedChange={setSkipSep} />
            <Label htmlFor="skip-sep" className="text-sm text-muted-foreground">
              跳过人声分离
            </Label>
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>
      </div>
    </div>
  )
}
