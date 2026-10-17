import { useEffect, useState } from "react"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { api } from "@/lib/api"
import { notifyError } from "@/lib/notify"
import { HugeiconsIcon } from "@hugeicons/react"
import { Loading03Icon } from "@hugeicons/core-free-icons"
import type { BatchPreview } from "@/lib/library-types"
import { libraryChanged } from "@/hooks/use-library-directory"

interface DeleteConfirmDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  archivePath: string
  taskId?: string | null
  taskDelete?: boolean
  onDeleted: () => void
}

export function DeleteConfirmDialog({
  open,
  onOpenChange,
  title,
  archivePath,
  taskId,
  taskDelete = false,
  onDeleted,
}: DeleteConfirmDialogProps) {
  const [deleting, setDeleting] = useState(false)
  const [preview, setPreview] = useState<BatchPreview | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    if (!open) return
    let cancelled = false
    setPreview(null); setError(null)
    void (async () => {
      let key: string | undefined
      if (archivePath) {
        try {
          const { archive } = await api.archives.get(archivePath)
          key = (archive as { content_key?: string }).content_key
        } catch { /* A live task may not have created its archive yet. */ }
      }
      if (!key && taskId) {
        key = (await api.library.keyForTask(taskId)).content_key
      }
      if (!key) throw new Error("无法确认内容标识，请刷新文件列表后重试。")
      const plan = await api.library.preview("delete", { type: "selected", content_keys: [key] })
      if (!cancelled) setPreview(plan)
    })().catch((reason) => { if (!cancelled) setError(String(reason)) })
    return () => { cancelled = true }
  }, [open, archivePath, taskId])

  const handleDelete = async () => {
    setDeleting(true)
    try {
      if (!preview?.targets.length) return
      for (const target of preview.targets) {
        const result = await api.library.deleteContent(target)
        if (result.outcome !== "deleted") throw new Error(`仍有 ${result.remaining_records} 条记录待删除。${result.errors.map((item) => item.error).join("；")}`)
      }
      libraryChanged()
      onOpenChange(false)
      onDeleted()
    } catch (err) {
      notifyError("删除失败", err)
    } finally {
      setDeleting(false)
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>确认删除</AlertDialogTitle>
          <AlertDialogDescription>
            {preview ? `将删除「${title}」的全部 ${preview.records} 条历史处理记录和生成文件。${preview.active_records || taskDelete ? "正在处理的任务会先停止。" : ""}此操作不可撤销。` : error || "正在确认全部历史记录…"}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter className="border-t-0">
          <AlertDialogCancel disabled={deleting}>取消</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            onClick={(event) => { event.preventDefault(); void handleDelete() }}
            disabled={deleting || !preview?.targets.length}
          >
            {deleting && <HugeiconsIcon icon={Loading03Icon} className="h-4 w-4 animate-spin mr-1" />}
            删除
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
