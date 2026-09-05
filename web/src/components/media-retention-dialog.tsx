import { useEffect, useState } from "react"
import { api } from "@/lib/api"
import { mediaPolicies, type MediaPolicy, type MediaRetentionPreview } from "@/lib/media-retention"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"

const roles: Record<string, string> = {
  source: "原始来源 / 播放", working: "转码中间文件", separated: "人声分离结果",
  segment: "识别分片", unknown: "用途待确认", external_source: "外部源文件",
}
const size = (bytes: number) => bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(2)} GiB`
  : bytes >= 1024 ** 2 ? `${(bytes / 1024 ** 2).toFixed(1)} MiB` : `${bytes.toLocaleString()} B`

export function MediaRetentionDialog({ archive, onClose, onApplied }: {
  archive: { path: string; title: string }
  onClose: () => void
  onApplied: () => void
}) {
  const [policy, setPolicy] = useState<MediaPolicy>("all")
  const [preview, setPreview] = useState<MediaRetentionPreview | null>(null)
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState("")
  useEffect(() => {
    let active = true
    api.mediaRetention.preview(archive.path).then(result => {
      if (active) { setPreview(result); setPolicy(result.policy) }
    }).catch(error => { if (active) setError(String(error)) })
      .finally(() => { if (active) setBusy(false) })
    return () => { active = false }
  }, [archive.path])

  const check = async () => {
    setBusy(true)
    setError("")
    try { setPreview(await api.mediaRetention.preview(archive.path, policy)) }
    catch (error) { setError(String(error)) }
    finally { setBusy(false) }
  }
  const apply = async () => {
    if (!preview) return
    setBusy(true)
    setError("")
    try {
      const result = await api.mediaRetention.apply(archive.path, preview.policy,
        preview.entries.filter(item => item.delete).map(item => item.path))
      setPreview(result)
      onApplied()
    } catch (error) { setError(String(error)) }
    finally { setBusy(false) }
  }
  const count = preview?.entries.filter(item => item.delete).length ?? 0
  return <Dialog open onOpenChange={open => { if (!open && !busy) onClose() }}>
    <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
      <DialogHeader>
        <DialogTitle>媒体保留</DialogTitle>
        <DialogDescription className="break-words">{archive.title}</DialogDescription>
      </DialogHeader>
      <label className="space-y-2 text-sm">
        <span>保留策略</span>
        <select aria-label="保留策略" value={policy} disabled={busy}
          className="h-10 w-full rounded-md border bg-background px-3"
          onChange={event => { setPolicy(event.target.value as MediaPolicy); setPreview(null) }}>
          {Object.entries(mediaPolicies).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
      <p className="text-sm text-muted-foreground">预览说明每个文件的用途、恢复条件及处理影响。执行后释放所列媒体占用。</p>
      {preview && <>
        <p className="text-sm" role="status">{preview.protected_reason || `可回收 ${size(preview.reclaimable_bytes)}，共 ${count} 个文件`}</p>
        {preview.reclaimed_bytes !== undefined && <p className="text-sm" role="status">已回收 {size(preview.reclaimed_bytes)}，清理 {preview.cleaned?.length ?? 0} 个文件</p>}
        <ul className="max-h-72 divide-y overflow-y-auto rounded-md border px-3">
          {preview.entries.map(item => <li key={item.path} className="space-y-1 py-3 text-sm">
            <div className="flex items-start justify-between gap-3"><span className="break-all font-medium">{item.path}</span><span className="shrink-0">{size(item.bytes)}</span></div>
            <p>{roles[item.role] ?? item.role} · {item.delete ? "待清理" : "保留"} · {item.reason}</p>
            {item.delete && <p className="text-xs text-muted-foreground">{item.recovery}。{item.impact}</p>}
          </li>)}
          {!preview.entries.length && <li className="py-3 text-sm text-muted-foreground">归档中没有媒体文件</li>}
        </ul>
        {preview.errors?.map(item => <p role="alert" key={item.path} className="break-all text-sm text-destructive">{item.path}：{item.error}</p>)}
      </>}
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      <DialogFooter>
        <Button variant="outline" disabled={busy} onClick={onClose}>关闭</Button>
        <Button variant="outline" disabled={busy} onClick={check}>{busy ? "处理中…" : "预览"}</Button>
        <Button variant="destructive" disabled={busy || !count} onClick={apply}>清理所列 {count} 个文件</Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
}
