import { useState, type FormEvent } from "react"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import { Label } from "@/components/ui/label"
import { api } from "@/lib/api"
import { FolderQueueDialog } from "@/components/folder-queue-dialog"
import { HugeiconsIcon } from "@hugeicons/react"
import { PlayIcon, Upload01Icon, Folder01Icon, Loading03Icon, ArrowDown01Icon, ArrowUp01Icon } from "@hugeicons/core-free-icons"

export function SubmitForm({ onSubmitted }: { onSubmitted?: () => void }) {
  const [source, setSource] = useState("")
  const [skipSep, setSkipSep] = useState(false)
  const [numSpeakers, setNumSpeakers] = useState("")
  const [hotwords, setHotwords] = useState("")
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [msg, setMsg] = useState("")
  const [showFolderDialog, setShowFolderDialog] = useState(false)

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

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    const val = source.trim()
    if (!val || submitting) return

    setSubmitting(true)
    setMsg("")
    try {
      const task = await api.tasks.create(val, buildOptions())
      setMsg(`\u2713 已提交 ${task.id.slice(0, 8)}`)
      setSource("")
      onSubmitted?.()
    } catch (err) {
      setMsg(`\u2717 ${err instanceof Error ? err.message : "提交失败"}`)
    } finally {
      setSubmitting(false)
    }
  }

  const handleFileUpload = async (file: File) => {
    setSubmitting(true)
    setMsg("")
    try {
      const { file_path } = await api.pipeline.upload(file)
      const task = await api.tasks.create(file_path, buildOptions())
      setMsg(`\u2713 已上传并提交 ${task.id.slice(0, 8)}`)
      onSubmitted?.()
    } catch (err) {
      setMsg(`\u2717 ${err instanceof Error ? err.message : "上传失败"}`)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="space-y-3">
      <FolderQueueDialog
        open={showFolderDialog}
        onOpenChange={setShowFolderDialog}
        options={buildOptions()}
        onSubmitted={() => {
          setMsg("✓ 文件夹任务已全部提交")
          onSubmitted?.()
        }}
      />
      <form onSubmit={handleSubmit} className="flex gap-2">
        <Input
          value={source}
          onChange={(e) => setSource(e.target.value)}
          placeholder="粘贴视频链接或本地文件路径\u2026"
          className="flex-1"
          disabled={submitting}
          autoComplete="off"
        />
        <Button type="submit" disabled={!source.trim() || submitting}>
          {submitting ? <HugeiconsIcon icon={Loading03Icon} className="h-4 w-4 animate-spin" /> : <HugeiconsIcon icon={PlayIcon} className="h-4 w-4" />}
          <span className="ml-1.5">处理</span>
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={submitting}
          onClick={() => {
            const input = document.createElement("input")
            input.type = "file"
            input.accept = "video/*,audio/*"
            input.onchange = () => {
              if (input.files?.[0]) handleFileUpload(input.files[0])
            }
            input.click()
          }}
        >
          <HugeiconsIcon icon={Upload01Icon} className="h-4 w-4" />
          <span className="ml-1.5 hidden sm:inline">上传</span>
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={submitting}
          onClick={() => setShowFolderDialog(true)}
          title="批量处理文件夹"
        >
          <HugeiconsIcon icon={Folder01Icon} className="h-4 w-4" />
          <span className="ml-1.5 hidden sm:inline">文件夹</span>
        </Button>
      </form>

      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <Switch id="skip-sep" checked={skipSep} onCheckedChange={setSkipSep} />
            <Label htmlFor="skip-sep" className="text-sm text-muted-foreground">
              跳过人声分离
            </Label>
          </div>
          <button
            type="button"
            className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground transition-colors"
            onClick={() => setShowAdvanced(!showAdvanced)}
          >
            {showAdvanced ? <HugeiconsIcon icon={ArrowUp01Icon} className="h-3.5 w-3.5" /> : <HugeiconsIcon icon={ArrowDown01Icon} className="h-3.5 w-3.5" />}
            高级选项
          </button>
        </div>
        {msg && (
          <p className={`text-sm ${msg.startsWith("\u2713") ? "text-emerald-600" : "text-destructive"}`}>
            {msg}
          </p>
        )}
      </div>

      {showAdvanced && (
        <div className="grid grid-cols-2 gap-3 p-3 rounded-md border bg-muted/30">
          <div className="space-y-1.5">
            <Label htmlFor="num-speakers" className="text-xs text-muted-foreground">
              说话人数量（留空自动检测）
            </Label>
            <Input
              id="num-speakers"
              type="number"
              min="1"
              max="20"
              value={numSpeakers}
              onChange={(e) => setNumSpeakers(e.target.value)}
              placeholder="自动"
              className="h-8"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="hotwords" className="text-xs text-muted-foreground">
              热词（逗号分隔，用于润色修正）
            </Label>
            <Input
              id="hotwords"
              value={hotwords}
              onChange={(e) => setHotwords(e.target.value)}
              placeholder="如: DeepSeek, GPT-4, 张三"
              className="h-8"
            />
          </div>
        </div>
      )}
    </div>
  )
}
