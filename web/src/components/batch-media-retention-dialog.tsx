import { useState } from "react"
import { HugeiconsIcon } from "@hugeicons/react"
import { Loading03Icon } from "@hugeicons/core-free-icons"

import { api } from "@/lib/api"
import { mediaPolicies, type MediaPolicy, type MediaRetentionPreview } from "@/lib/media-retention"
import { errorMessage } from "@/lib/notify"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"

const size = (bytes: number) => bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(2)} GiB`
  : bytes >= 1024 ** 2 ? `${(bytes / 1024 ** 2).toFixed(1)} MiB` : `${bytes.toLocaleString()} B`

interface Planned {
  archive: { path: string; title: string }
  preview?: MediaRetentionPreview
  error?: string
}

/** Media retention for several archives: preview everything first, then apply in one go. */
export function BatchMediaRetentionDialog({ archives, onClose, onApplied }: {
  archives: Array<{ path: string; title: string }>
  onClose: () => void
  onApplied: () => void
}) {
  const [policy, setPolicy] = useState<MediaPolicy>("playback")
  const [phase, setPhase] = useState<"idle" | "previewing" | "ready" | "applying" | "done">("idle")
  const [progress, setProgress] = useState(0)
  const [plan, setPlan] = useState<Planned[]>([])
  const [result, setResult] = useState<{ reclaimed: number; failed: string[] } | null>(null)
  const busy = phase === "previewing" || phase === "applying"

  const runPreview = async () => {
    setPhase("previewing")
    setProgress(0)
    const next: Planned[] = []
    for (const archive of archives) {
      try {
        next.push({ archive, preview: await api.mediaRetention.preview(archive.path, policy) })
      } catch (error) {
        next.push({ archive, error: errorMessage(error) })
      }
      setProgress(next.length)
    }
    setPlan(next)
    setPhase("ready")
  }

  const deletable = plan.filter((item) => item.preview && !item.preview.protected_reason
    && item.preview.entries.some((entry) => entry.delete))
  const reclaimable = deletable.reduce((sum, item) => sum + (item.preview?.reclaimable_bytes ?? 0), 0)
  const files = deletable.reduce((sum, item) => sum + (item.preview?.entries.filter((entry) => entry.delete).length ?? 0), 0)
  const protectedCount = plan.filter((item) => item.preview?.protected_reason).length
  const errorCount = plan.filter((item) => item.error).length

  const runApply = async () => {
    setPhase("applying")
    setProgress(0)
    let reclaimed = 0
    const failed: string[] = []
    for (const [index, item] of deletable.entries()) {
      try {
        const applied = await api.mediaRetention.apply(item.archive.path, policy,
          item.preview!.entries.filter((entry) => entry.delete).map((entry) => entry.path))
        reclaimed += applied.reclaimed_bytes ?? 0
        if (applied.errors?.length) failed.push(item.archive.title)
      } catch {
        failed.push(item.archive.title)
      }
      setProgress(index + 1)
    }
    setResult({ reclaimed, failed })
    setPhase("done")
    onApplied()
  }

  return (
    <Dialog open onOpenChange={(open) => { if (!open && !busy) onClose() }}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>媒体保留 · {archives.length} 项</DialogTitle>
          <DialogDescription>先预览每个条目能释放多少空间，确认后再统一清理。</DialogDescription>
        </DialogHeader>

        <label className="space-y-2 text-sm">
          <span>保留策略</span>
          <select
            aria-label="保留策略"
            value={policy}
            disabled={busy || phase === "done"}
            className="h-10 w-full rounded-md border bg-background px-3"
            onChange={(event) => { setPolicy(event.target.value as MediaPolicy); setPlan([]); setPhase("idle") }}
          >
            {Object.entries(mediaPolicies).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>

        <div className="min-h-12 text-sm" role="status">
          {phase === "previewing" && `正在预览 ${progress} / ${archives.length}…`}
          {phase === "applying" && `正在清理 ${progress} / ${deletable.length}…`}
          {phase === "ready" && (
            <div className="space-y-1">
              <p>可回收 <b>{size(reclaimable)}</b>，涉及 {deletable.length} 个条目的 {files} 个文件。</p>
              {(protectedCount > 0 || errorCount > 0) && (
                <p className="text-xs text-muted-foreground">
                  {protectedCount > 0 && `${protectedCount} 个条目受保护（例如仍在处理），不会改动。`}
                  {errorCount > 0 && ` ${errorCount} 个条目预览失败，会跳过。`}
                </p>
              )}
            </div>
          )}
          {phase === "done" && result && (
            <div className="space-y-1">
              <p>已回收 <b>{size(result.reclaimed)}</b>。</p>
              {result.failed.length > 0 && (
                <p className="text-xs text-destructive">{result.failed.length} 个条目没能完全清理：{result.failed.slice(0, 3).join("、")}</p>
              )}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" disabled={busy} onClick={onClose}>{phase === "done" ? "关闭" : "取消"}</Button>
          {phase !== "done" && (phase === "ready" ? (
            <Button variant="destructive" disabled={busy || deletable.length === 0} onClick={() => void runApply()}>
              清理 {files} 个文件
            </Button>
          ) : (
            <Button disabled={busy} onClick={() => void runPreview()}>
              {phase === "previewing" && <HugeiconsIcon icon={Loading03Icon} className="size-4 animate-spin" />}
              预览
            </Button>
          ))}
          {phase === "applying" && <HugeiconsIcon icon={Loading03Icon} className="size-4 animate-spin self-center" />}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
