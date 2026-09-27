import { useState } from "react"
import { HugeiconsIcon } from "@hugeicons/react"
import { Task01Icon } from "@hugeicons/core-free-icons"

import { cn } from "@/lib/utils"
import { useActiveTasks } from "@/hooks/use-active-tasks"
import { ActivityContent } from "@/components/activity/activity-content"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"

/** Header button + side panel with everything that is running, waiting, paused or failed. */
export function ActivityPanel() {
  const [open, setOpen] = useState(false)
  const { tasks } = useActiveTasks()
  const running = tasks.filter((task) => task.status === "processing").length
  const count = tasks.length

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label={count ? `活动：${count} 个任务` : "活动"}
        title="活动：进行中、排队、暂停和失败的任务"
        className={cn(
          "flex min-h-11 items-center gap-1.5 rounded-md px-2 text-xs transition-colors md:min-h-9",
          open ? "bg-primary/10 text-primary" : "text-muted-foreground hover:bg-muted hover:text-foreground",
        )}
      >
        <HugeiconsIcon icon={Task01Icon} className="size-4" />
        <span className="hidden lg:inline">活动</span>
        {count > 0 && (
          <span className={cn(
            "inline-flex h-[1.1rem] min-w-[1.1rem] items-center justify-center rounded-full px-1 text-[10px] font-medium leading-none tabular-nums",
            running ? "bg-primary text-primary-foreground" : "bg-muted-foreground/20 text-foreground",
          )}>
            {count}
          </span>
        )}
      </button>

      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent side="right" className="w-full gap-0 sm:max-w-md">
          <SheetHeader className="border-b pr-12">
            <SheetTitle>活动</SheetTitle>
            <SheetDescription>正在处理、排队、暂停和最近失败的任务</SheetDescription>
          </SheetHeader>
          <div className="min-h-0 flex-1 overflow-y-auto p-2">
            {open && <ActivityContent onNavigate={() => setOpen(false)} />}
          </div>
        </SheetContent>
      </Sheet>
    </>
  )
}
