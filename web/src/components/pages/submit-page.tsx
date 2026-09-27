import { useEffect } from "react"
import { HugeiconsIcon } from "@hugeicons/react"
import { Clock01Icon, Link01Icon } from "@hugeicons/core-free-icons"

import { useSubmitHistory } from "@/hooks/use-submit-history"
import { openComposer } from "@/lib/composer-store"
import { isDesktopApp } from "@/lib/desktop-bridge"
import { notifySuccess } from "@/lib/notify"
import { openTask } from "@/lib/open-task"
import { navigate } from "@/lib/router"
import { usePlatform } from "@/platform/use-platform"
import { SourceComposer } from "@/components/composer/source-composer"

/** The 处理 page: the same composer as the Ctrl+N dialog, with room to breathe and recent links on phones. */
export function SubmitPage() {
  const platform = usePlatform()
  const submitHistory = useSubmitHistory()

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

        <section className="space-y-2 md:hidden" aria-labelledby="recent-submit-title">
          <div className="flex items-center justify-between gap-3">
            <h2 id="recent-submit-title" className="flex items-center gap-2 text-sm font-medium">
              <HugeiconsIcon icon={Clock01Icon} className="size-4 text-muted-foreground" />
              最近提交
            </h2>
            {submitHistory.items.length > 0 ? (
              <button type="button" className="min-h-11 px-2 text-xs text-muted-foreground" onClick={submitHistory.clear}>
                清空
              </button>
            ) : null}
          </div>
          {submitHistory.items.length > 0 ? (
            <div className="divide-y rounded-lg border bg-card">
              {submitHistory.items.map((item) => (
                <button
                  type="button"
                  key={`${item.source}-${item.submittedAt}`}
                  className="flex min-h-12 w-full items-center gap-3 px-3 text-left active:bg-muted"
                  onClick={() => openComposer({ text: item.source })}
                >
                  <HugeiconsIcon icon={Link01Icon} className="size-4 shrink-0 text-muted-foreground" />
                  <span className="min-w-0 flex-1 truncate text-sm">{item.source}</span>
                  <span className="shrink-0 text-xs text-muted-foreground">
                    {new Date(item.submittedAt).toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" })}
                  </span>
                </button>
              ))}
            </div>
          ) : (
            <p className="rounded-lg border border-dashed px-4 py-5 text-center text-sm text-muted-foreground">
              成功提交链接后会显示在这里。
            </p>
          )}
        </section>
      </div>
    </div>
  )
}
