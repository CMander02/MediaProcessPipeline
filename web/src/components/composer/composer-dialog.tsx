import { closeComposer, useComposerState } from "@/lib/composer-store"
import { notifySuccess } from "@/lib/notify"
import { openTask } from "@/lib/open-task"
import { taskDisplayTitle } from "@/lib/task-display"
import type { Task } from "@/lib/api"
import { SourceComposer } from "@/components/composer/source-composer"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"

function announce(tasks: Task[]) {
  if (tasks.length === 0) return
  const [first] = tasks
  notifySuccess(
    tasks.length === 1 ? "已加入处理队列" : `${tasks.length} 项已加入处理队列`,
    tasks.length === 1 ? taskDisplayTitle(first) : "进度在标题栏「活动」里查看。",
    { label: "查看", onClick: () => openTask(first) },
  )
}

/** New processing, opened with Ctrl+N, by pasting a link or dropping files anywhere. */
export function ComposerDialog() {
  const { open } = useComposerState()

  return (
    <Dialog open={open} onOpenChange={(next) => { if (!next) closeComposer() }}>
      <DialogContent
        // grid-cols-1 caps the column at the dialog width, so long titles truncate instead of widening it.
        className="max-h-[92dvh] grid-cols-1 gap-3 overflow-y-auto sm:max-w-2xl"
        // Keep a half-written list when clicking outside by accident; Esc and 取消 still close it.
        onInteractOutside={(event) => event.preventDefault()}
      >
        <DialogHeader>
          <DialogTitle>新建处理</DialogTitle>
          <DialogDescription className="sr-only">粘贴链接或添加音视频文件，然后开始处理</DialogDescription>
        </DialogHeader>
        {open && (
          <SourceComposer
            mode="dialog"
            onCancel={closeComposer}
            onNavigate={closeComposer}
            onSubmitted={(tasks) => {
              closeComposer()
              announce(tasks)
            }}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}
