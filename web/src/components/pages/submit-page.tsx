import { useEffect } from "react"
import { openComposer } from "@/lib/composer-store"
import { isDesktopApp } from "@/lib/desktop-bridge"
import { notifySuccess } from "@/lib/notify"
import { openTask } from "@/lib/open-task"
import { navigate } from "@/lib/router"
import { usePlatform } from "@/platform/use-platform"
import { RecentSubmissions } from "@/components/composer/recent-submissions"
import { SourceComposer } from "@/components/composer/source-composer"

/** The 处理 page: the same composer as the Ctrl+N dialog, with room to breathe and the latest tasks below. */
export function SubmitPage() {
  const platform = usePlatform()

  // Text shared to the Android app lands here.
  useEffect(() => {
    const pending = platform.consumeSharedText()
    if (pending) openComposer({ text: pending })
    const handleShare = (event: Event) => {
      const text = (event as CustomEvent<{ text?: string }>).detail?.text?.trim()
      if (text) openComposer({ text })
    }
    window.addEventListener("mpp:share-received", handleShare)
    return () => window.removeEventListener("mpp:share-received", handleShare)
  }, [platform])

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-5 px-4 py-5 sm:px-6 md:py-8">
        <div className="hidden md:block">
          <h1 className="text-lg font-semibold">新建处理</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            在任何页面都可以直接粘贴链接或拖入文件开始处理{isDesktopApp ? "，或按 Ctrl+N" : ""}。
          </p>
        </div>

        <SourceComposer
          mode="page"
          onSubmitted={(tasks) => {
            // One item: follow it live. Several: stay here and let 活动 show progress.
            if (tasks.length === 1) {
              navigate(`#/result/task/${tasks[0].id}`)
              return
            }
            if (tasks.length > 1) {
              notifySuccess(`${tasks.length} 项已加入处理队列`, "进度在标题栏「活动」里查看。", {
                label: "查看第一项",
                onClick: () => openTask(tasks[0]),
              })
            }
          }}
        />

        <RecentSubmissions />
      </div>
    </div>
  )
}
