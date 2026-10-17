import { useState, type ReactNode } from "react"
import { api } from "@/lib/api"
import type { BatchAction, BatchPreview, BatchScope } from "@/lib/library-types"
import { useLibraryDirectory, libraryChanged } from "@/hooks/use-library-directory"
import { refreshActiveTasks } from "@/hooks/use-active-tasks"
import { archiveExportEntries, uniqueFolders } from "@/lib/archive-export"
import { createZip, downloadBytes } from "@/lib/zip"
import { errorMessage, notifyError } from "@/lib/notify"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { BatchMediaRetentionDialog } from "@/components/batch-media-retention-dialog"
import type { ArchiveItem } from "@/repositories/archive-types"
import { useAppAccess } from "@/hooks/use-app-access-context"

const labels: Record<BatchAction, string> = { retry: "继续 / 重试", checkpoint_rerun: "从断点继续", full_rerun: "全部重新处理", delete: "删除内容", export: "导出", retention: "媒体保留", move: "移到文件夹" }
export type OnBatchCompleted = (keys: string[], action: BatchAction, folderId?: string | null) => void

/** Batch actions for a scope; callers place the action list in any menu and render the dialogs once. */
export function useLibraryBatchActions(scope: BatchScope, onCompleted?: OnBatchCompleted): {
  actions: Array<{ action: BatchAction; label: string; destructive: boolean }>
  open: (action: BatchAction) => void
  disabled: boolean
  dialogs: ReactNode
} {
  const { folders } = useLibraryDirectory()
  const { online, capabilities } = useAppAccess()
  const [plan, setPlan] = useState<BatchPreview | null>(null)
  const [busy, setBusy] = useState(false)
  const [progress, setProgress] = useState(0)
  const [destination, setDestination] = useState("")
  const [result, setResult] = useState<{ done: number; failures: string[] } | null>(null)
  const [retention, setRetention] = useState<ArchiveItem[] | null>(null)
  const open = async (action: BatchAction) => {
    setBusy(true)
    try {
      const preview = await api.library.preview(action, scope)
      setPlan(preview); setResult(null); setProgress(0); setDestination("")
    } catch (error) { notifyError("读取操作范围失败", error) }
    finally { setBusy(false) }
  }
  const run = async () => {
    if (!plan || busy) return
    setBusy(true)
    const done: string[] = [], failures: string[] = []
    try {
      if (plan.action === "retention") {
        setRetention(plan.targets.flatMap((target) => target.archive ? [target.archive] : []))
        setPlan(null)
        return
      }
      if (plan.action === "export") {
        const archives = plan.targets.flatMap((target) => target.archive ? [target.archive] : [])
        const names = uniqueFolders(archives.map((archive) => archive.title))
        const entries = []
        for (const [index, archive] of archives.entries()) {
          try {
            const exported = await archiveExportEntries(archive, names[index])
            if (!exported.length) throw new Error("没有可导出的摘要、导图或字幕")
            entries.push(...exported); done.push(plan.targets[index].content_key)
          }
          catch (error) { failures.push(`${archive.title}：${errorMessage(error)}`) }
          setProgress(index + 1)
        }
        if (entries.length) downloadBytes(`MPP 导出 ${new Date().toISOString().slice(0, 10)}.zip`, createZip(entries))
      } else {
        for (const [index, target] of plan.targets.entries()) {
          try {
            if (plan.action === "move") {
              const moved = await api.library.move([target.content_key], destination || null)
              if (!moved.items.some((item) => item.content_key === target.content_key && ["moved", "unchanged"].includes(item.outcome))) throw new Error(moved.items[0]?.reason || "移动未完成")
            }
            else if (plan.action === "delete") {
              const deleted = await api.library.deleteContent(target)
              if (deleted.outcome !== "deleted") throw new Error(`仍有 ${deleted.remaining_records} 条记录待删除；${deleted.errors.map((item) => item.error).join("；")}`)
            } else if (target.task_id) {
              if (plan.action === "full_rerun") await api.tasks.fullRerun(target.task_id)
              else if (target.status === "paused") await api.tasks.resume(target.task_id)
              else await api.tasks.checkpointRerun(target.task_id)
            }
            done.push(target.content_key)
          } catch (error) { failures.push(`${target.title || target.content_key}：${errorMessage(error)}`) }
          setProgress(index + 1)
        }
      }
      setResult({ done: done.length, failures })
      onCompleted?.(done, plan.action, plan.action === "move" ? destination || null : undefined)
      libraryChanged(); void refreshActiveTasks()
    } catch (error) {
      setResult({ done: 0, failures: [errorMessage(error)] })
    } finally { setBusy(false) }
  }
  const actions = (Object.keys(labels) as BatchAction[])
    .filter((action) => capabilities.archive_mutation || ["retry", "checkpoint_rerun", "full_rerun", "export"].includes(action))
    .map((action) => ({ action, label: labels[action], destructive: action === "delete" }))
  const dialogs = <>
    <Dialog open={plan !== null} onOpenChange={(open) => { if (!open && !busy) setPlan(null) }}>
      <DialogContent onInteractOutside={(event) => { if (busy) event.preventDefault() }} onEscapeKeyDown={(event) => { if (busy) event.preventDefault() }}>
        <DialogHeader><DialogTitle>{plan ? labels[plan.action] : "批量操作"}</DialogTitle><DialogDescription>{plan?.contents} 项内容 · {plan?.records} 条处理记录。可以执行 {plan?.runnable} 项，跳过 {plan?.skipped_items.length} 项。</DialogDescription></DialogHeader>
        {plan?.action === "delete" && <p className="text-sm">会删除所选内容的全部历史记录和生成文件。{plan.active_records > 0 && `${plan.active_records} 个处理中任务会先停止。`}此操作不可撤销。</p>}
        {plan?.action === "full_rerun" && <p className="text-sm">清除所选任务已生成的结果，再从头处理。正在处理的内容会跳过。</p>}
        {plan?.action === "move" && <label className="space-y-2 text-sm">目标位置<select aria-label="目标位置" className="block w-full rounded-md border bg-background p-2" value={destination} onChange={(event) => setDestination(event.target.value)}><option value="">未分类</option>{folders.map((folder) => <option key={folder.id} value={folder.id}>{folder.name}</option>)}</select></label>}
        <div className="max-h-60 overflow-y-auto space-y-1 text-sm">
          {plan?.targets.map((target) => <p key={target.content_key} className="truncate">{target.title || target.content_key}{plan.action === "delete" ? `（${target.records} 条记录）` : ""}</p>)}
          {plan?.skipped_items.map((item) => <p key={item.content_key} className="text-muted-foreground">跳过：{item.title || item.content_key} · {item.reason}</p>)}
        </div>
        {busy && <p role="status" className="text-sm">已处理 {progress} / {plan?.runnable}</p>}
        {result && <div role="status" className="space-y-1 text-sm"><p>成功 {result.done} 项 · 失败 {result.failures.length} 项</p>{result.failures.map((failure, index) => <p key={index} className="text-destructive">{failure}</p>)}</div>}
        <DialogFooter><Button variant="ghost" disabled={busy} onClick={() => setPlan(null)}>{result ? "关闭" : "取消"}</Button>{!result && <Button variant={plan?.action === "delete" ? "destructive" : "default"} disabled={busy || !plan?.runnable} onClick={() => void run()}>确认{plan ? labels[plan.action] : ""}</Button>}</DialogFooter>
      </DialogContent>
    </Dialog>
    {retention && <BatchMediaRetentionDialog archives={retention} onClose={() => setRetention(null)} onApplied={libraryChanged} />}
  </>
  return { actions, open: (action) => void open(action), dialogs,
    disabled: !online || busy || (scope.type === "selected" && !scope.content_keys.length) }
}
