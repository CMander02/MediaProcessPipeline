import { closeComposer, useComposerState } from "@/lib/composer-store"
import { notifySuccess } from "@/lib/notify"
import { openTask } from "@/lib/open-task"
import { taskDisplayTitle } from "@/lib/task-display"
import type { ImportSummary, Task } from "@/lib/api"
import { SourceComposer } from "@/components/composer/source-composer"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"

function announce(tasks: Task[], summary?: ImportSummary) {
  if (summary && (summary.reused > 0 || summary.active > 0)) {
    const detail = [
      summary.created > 0 && `新增处理 ${summary.created} 项`,
      summary.reused > 0 && `复用已有 ${summary.reused} 项`,
      summary.active > 0 && `处理中 ${summary.active} 项`,
    ].filter(Boolean).join("，")
    notifySuccess("导入完成", detail, tasks.length > 0
      ? { label: "查看", onClick: () => openTask(tasks[0]) }
      : undefined)
    return
  }
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
        className="flex max-h-[92dvh] min-w-0 flex-col gap-4 overflow-hidden p-5 text-base sm:max-w-4xl sm:p-6"
        // Keep a half-written list when clicking outside by accident; Esc and 取消 still close it.
        onInteractOutside={(event) => event.preventDefault()}
      >
        <DialogHeader className="shrink-0">
          <DialogTitle className="text-lg">新建处理</DialogTitle>
          <DialogDescription className="sr-only">粘贴链接或添加音视频文件，然后开始处理</DialogDescription>
        </DialogHeader>
        {open && (
          <SourceComposer
            mode="dialog"
            onCancel={closeComposer}
            onNavigate={closeComposer}
            onSubmitted={(tasks, summary) => {
              closeComposer()
              announce(tasks, summary)
            }}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}
