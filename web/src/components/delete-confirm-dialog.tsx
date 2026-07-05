import { useState } from "react"
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
import { HugeiconsIcon } from "@hugeicons/react"
import { Loading03Icon } from "@hugeicons/core-free-icons"

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

  const handleDelete = async () => {
    setDeleting(true)
    try {
      if (taskDelete && taskId) {
        await api.tasks.delete(taskId)
      } else {
        await api.archives.delete(archivePath)
      }
      onOpenChange(false)
      onDeleted()
    } catch (err) {
      console.error("Delete failed:", err)
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
            {taskDelete
              ? `将停止任务并删除「${title}」的任务记录和已生成文件，此操作不可撤销。`
              : `将删除「${title}」的所有文件（归档、转录、摘要等），此操作不可撤销。`}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter className="border-t-0">
          <AlertDialogCancel disabled={deleting}>取消</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            onClick={handleDelete}
            disabled={deleting}
          >
            {deleting && <HugeiconsIcon icon={Loading03Icon} className="h-4 w-4 animate-spin mr-1" />}
            删除
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
