import { useEffect, useState } from "react"
import { HugeiconsIcon } from "@hugeicons/react"
import { Clock01Icon } from "@hugeicons/core-free-icons"

import { ActivityTaskRow } from "@/components/activity/activity-task-row"
import { useActiveTasks } from "@/hooks/use-active-tasks"
import { api, type Task } from "@/lib/api"
import { openTask } from "@/lib/open-task"

const SHOWN = 8

/** The latest tasks with their progress, so what was just added stays in view on the 处理 page. */
export function RecentSubmissions() {
  const { tasks: active } = useActiveTasks()
  const [tasks, setTasks] = useState<Task[] | null>(null)
  // The active-task store follows task events; reload whenever anything in it moves.
  const moved = active.map((task) => `${task.id}:${task.status}:${Math.round((task.progress ?? 0) * 100)}`).join("|")

  useEffect(() => {
    let cancelled = false
    api.tasks.list(undefined, SHOWN)
      .then((list) => { if (!cancelled) setTasks(list) })
      .catch(() => { if (!cancelled) setTasks((current) => current ?? []) })
    return () => { cancelled = true }
  }, [moved])

  return (
    <section className="flex flex-col gap-2" aria-labelledby="recent-submit-title">
      <h2 id="recent-submit-title" className="flex items-center gap-2 text-sm font-medium">
        <HugeiconsIcon icon={Clock01Icon} className="size-4 text-muted-foreground" />
        最近提交
      </h2>
      {tasks === null ? null : tasks.length > 0 ? (
        <div className="rounded-lg border bg-card p-1">
          {tasks.map((task) => (
            <ActivityTaskRow key={task.id} task={task} onOpen={() => openTask(task)} />
          ))}
        </div>
      ) : (
        <p className="rounded-lg border border-dashed px-4 py-5 text-center text-sm text-muted-foreground">
          提交后会列在这里，可以随时看进度。
        </p>
      )}
    </section>
  )
}
