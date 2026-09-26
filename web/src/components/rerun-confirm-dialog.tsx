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

interface RerunConfirmDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  onConfirm: () => void
}

/** Full rerun deletes generated outputs (see queue.rerun_full), so it always asks first. */
export function RerunConfirmDialog({ open, onOpenChange, title, onConfirm }: RerunConfirmDialogProps) {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>全部重新处理？</AlertDialogTitle>
          <AlertDialogDescription>
            「{title}」已生成的字幕（包括你手动改过的内容）、说话人、摘要和导图会被删除，然后从头重新处理。原始媒体、下载的平台字幕和元数据会保留。
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter className="border-t-0">
          <AlertDialogCancel>取消</AlertDialogCancel>
          <AlertDialogAction variant="destructive" onClick={onConfirm}>全部重新处理</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
