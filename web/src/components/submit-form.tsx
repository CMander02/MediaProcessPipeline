import { useState, type FormEvent } from "react"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import { Label } from "@/components/ui/label"
import { api } from "@/lib/api"
import { Play, Upload, Loader2 } from "lucide-react"

export function SubmitForm({ onSubmitted }: { onSubmitted?: () => void }) {
  const [source, setSource] = useState("")
  const [skipSep, setSkipSep] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [msg, setMsg] = useState("")

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    const val = source.trim()
    if (!val || submitting) return

    setSubmitting(true)
    setMsg("")
    try {
      const task = await api.tasks.create(val, skipSep ? { skip_separation: true } : {})
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
      const task = await api.tasks.create(file_path, skipSep ? { skip_separation: true } : {})
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
          {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
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
          <Upload className="h-4 w-4" />
          <span className="ml-1.5 hidden sm:inline">上传</span>
        </Button>
      </form>

      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Switch id="skip-sep" checked={skipSep} onCheckedChange={setSkipSep} />
          <Label htmlFor="skip-sep" className="text-sm text-muted-foreground">
            跳过人声分离
          </Label>
        </div>
        {msg && (
          <p className={`text-sm ${msg.startsWith("\u2713") ? "text-emerald-600" : "text-destructive"}`}>
            {msg}
          </p>
        )}
      </div>
    </div>
  )
}
