import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react"
import { HugeiconsIcon } from "@hugeicons/react"
import { Loading03Icon } from "@hugeicons/core-free-icons"

import { api, type Task } from "@/lib/api"
import { notifyError, notifySuccess } from "@/lib/notify"
import { taskDisplayTitle } from "@/lib/task-display"
import { refreshActiveTasks, useActiveTasks } from "@/hooks/use-active-tasks"
import { openTask } from "@/lib/open-task"
import { isDesktopApp } from "@/lib/desktop-bridge"
import { ActivityTaskRow, type TaskRowAction } from "@/components/activity/activity-task-row"
import { Button } from "@/components/ui/button"
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

const RECENT_FAILED = 20
const RECENT_DONE = 8

interface RecentTasks {
  failed: Task[]
  completed: Task[]
  /** Failed task ids whose source already has a finished archive */
  superseded: Set<string>
  loaded: boolean
}

const byUpdated = (a: Task, b: Task) => Date.parse(b.updated_at) - Date.parse(a.updated_at)

async function loadRecentTasks(): Promise<RecentTasks> {
  const [failed, cancelled, completed] = await Promise.all([
    api.tasks.list("failed", RECENT_FAILED),
    api.tasks.list("cancelled", RECENT_FAILED),
    api.tasks.list("completed", RECENT_DONE),
  ])
  const stopped = [...failed, ...cancelled].sort(byUpdated).slice(0, RECENT_FAILED)
  const superseded = new Set<string>()
  const sources = [...new Set(stopped.map((task) => task.source).filter((source) => /^https?:\/\//i.test(source)))]
  if (sources.length) {
    try {
      const { matches } = await api.archives.lookup(sources)
      for (const task of stopped) {
        if (matches[task.source]?.some((match) => match.status === "completed" && !match.processing)) superseded.add(task.id)
      }
    } catch {
      // Without the lookup every failed run is simply offered for retry.
    }
  }
  return { failed: stopped, completed: completed.sort(byUpdated), superseded, loaded: true }
}

/** Recently failed and finished tasks; reloaded whenever the set of active tasks changes. */
function useRecentTasks(activeKey: string): RecentTasks & { reload: () => void } {
  const [state, setState] = useState<RecentTasks>({ failed: [], completed: [], superseded: new Set(), loaded: false })
  const [nonce, setNonce] = useState(0)

  useEffect(() => {
    let cancelled = false
    loadRecentTasks()
      .then((next) => { if (!cancelled) setState(next) })
      .catch(() => { if (!cancelled) setState((current) => ({ ...current, loaded: true })) })
    return () => { cancelled = true }
  }, [activeKey, nonce])

  const reload = useCallback(() => setNonce((value) => value + 1), [])
  return { ...state, reload }
}

function Section({ title, count, action, children }: { title: string; count: number; action?: ReactNode; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-0.5" aria-label={title}>
      <div className="flex h-8 items-center gap-2 px-2">
        <h3 className="text-xs font-medium text-muted-foreground">{title}</h3>
        <span className="text-xs tabular-nums text-muted-foreground">{count}</span>
        <span className="flex-1" />
        {action}
      </div>
      {children}
    </section>
  )
}

interface ActivityContentProps {
  /** Called before navigating away, e.g. to close the panel */
  onNavigate?: () => void
}

/** 进行中 / 排队 / 已暂停 / 失败 / 最近完成, with the same row and bulk actions everywhere. */
export function ActivityContent({ onNavigate }: ActivityContentProps) {
  const { tasks: active, loaded: activeLoaded } = useActiveTasks()
  const activeKey = active.map((task) => `${task.id}:${task.status}`).sort().join(",")
  const recent = useRecentTasks(activeKey)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [bulk, setBulk] = useState<"resume" | "retry" | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<Task | null>(null)
  const [confirmRetryAll, setConfirmRetryAll] = useState(false)

  const processing = active.filter((task) => task.status === "processing")
  const queued = active.filter((task) => task.status === "queued" || task.status === "pending")
  const paused = active.filter((task) => task.status === "paused")
  const activeIds = useMemo(() => new Set(active.map((task) => task.id)), [active])
  const failed = recent.failed.filter((task) => !activeIds.has(task.id))
  const retryable = failed.filter((task) => !recent.superseded.has(task.id))

  const { reload: reloadRecent } = recent
  const afterChange = useCallback(async () => {
    reloadRecent()
    await refreshActiveTasks()
  }, [reloadRecent])

  const open = (task: Task) => {
    onNavigate?.()
    openTask(task)
  }

  const runAction = async (task: Task, action: TaskRowAction) => {
    if (action === "delete") {
      setDeleteTarget(task)
      return
    }
    setBusyId(task.id)
    try {
      if (action === "pause") await api.tasks.pause(task.id)
      if (action === "resume") await api.tasks.resume(task.id)
      if (action === "retry") await api.tasks.checkpointRerun(task.id)
      await afterChange()
    } catch (error) {
      notifyError(action === "pause" ? "暂停失败" : action === "resume" ? "继续处理失败" : "重试失败", error)
    } finally {
      setBusyId(null)
    }
  }

  const runDelete = async () => {
    const task = deleteTarget
    if (!task) return
    setBusyId(task.id)
    try {
      await api.tasks.delete(task.id)
      setDeleteTarget(null)
      await afterChange()
    } catch (error) {
      notifyError("删除失败", error)
    } finally {
      setBusyId(null)
    }
  }

  const runBulk = async (kind: "resume" | "retry") => {
    const targets = kind === "resume" ? paused : retryable
    if (bulk || targets.length === 0) return
    setBulk(kind)
    let done = 0
    for (const task of targets) {
      try {
        if (kind === "resume") await api.tasks.resume(task.id)
        else await api.tasks.checkpointRerun(task.id)
        done += 1
      } catch {
        // Counted below; the row stays in its section so it can be retried alone.
      }
    }
    setBulk(null)
    if (done) notifySuccess(kind === "resume" ? `已继续 ${done} 个任务` : `${done} 个任务已从断点重新排队`)
    if (done < targets.length) notifyError(`${targets.length - done} 个任务没能${kind === "resume" ? "继续" : "重试"}`)
    await afterChange()
  }

  const row = (task: Task, note?: string) => (
    <ActivityTaskRow
      key={task.id}
      task={task}
      note={note}
      busy={busyId === task.id}
      onOpen={() => open(task)}
      onAction={(action) => void runAction(task, action)}
    />
  )

  const nothingActive = activeLoaded && active.length === 0

  return (
    <div className="flex flex-col gap-3">
      {nothingActive && (
        <p className="rounded-md border border-dashed px-3 py-4 text-center text-[13px] text-muted-foreground">
          现在没有正在处理的任务。{isDesktopApp ? "按 Ctrl+N、" : ""}把链接粘贴到文件库里或把音视频拖进窗口即可新建。
        </p>
      )}
      {processing.length > 0 && <Section title="进行中" count={processing.length}>{processing.map((task) => row(task))}</Section>}
      {queued.length > 0 && <Section title="排队" count={queued.length}>{queued.map((task) => row(task))}</Section>}
      {paused.length > 0 && (
        <Section
          title="已暂停"
          count={paused.length}
          action={paused.length > 1 ? (
            <Button type="button" size="sm" variant="ghost" className="h-7 px-2 text-xs" disabled={bulk !== null} onClick={() => void runBulk("resume")}>
              {bulk === "resume" && <HugeiconsIcon icon={Loading03Icon} className="size-3.5 animate-spin" />}
              全部继续
            </Button>
          ) : undefined}
        >
          {paused.map((task) => row(task))}
        </Section>
      )}
      {failed.length > 0 && (
        <Section
          title="失败（最近）"
          count={failed.length}
          action={retryable.length > 0 ? (
            <Button type="button" size="sm" variant="ghost" className="h-7 px-2 text-xs" disabled={bulk !== null} onClick={() => setConfirmRetryAll(true)}>
              {bulk === "retry" && <HugeiconsIcon icon={Loading03Icon} className="size-3.5 animate-spin" />}
              全部重试…
            </Button>
          ) : undefined}
        >
          {failed.map((task) => row(task, recent.superseded.has(task.id) ? "已有成功版本" : undefined))}
        </Section>
      )}
      {recent.completed.length > 0 && (
        <Section title="最近完成" count={recent.completed.length}>
          {recent.completed.map((task) => (
            <ActivityTaskRow key={task.id} task={task} onOpen={() => open(task)} />
          ))}
        </Section>
      )}

      <AlertDialog open={deleteTarget !== null} onOpenChange={(next) => { if (!next && busyId !== deleteTarget?.id) setDeleteTarget(null) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除任务？</AlertDialogTitle>
            <AlertDialogDescription>
              {deleteTarget ? `将停止「${taskDisplayTitle(deleteTarget)}」并删除已生成的文件。此操作不可撤销。` : ""}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter className="border-t-0">
            <AlertDialogCancel disabled={busyId === deleteTarget?.id}>取消</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={busyId === deleteTarget?.id}
              onClick={(event) => { event.preventDefault(); void runDelete() }}
            >
              {busyId === deleteTarget?.id && <HugeiconsIcon icon={Loading03Icon} className="size-4 animate-spin" />}
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={confirmRetryAll} onOpenChange={setConfirmRetryAll}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>重试 {retryable.length} 个失败的任务？</AlertDialogTitle>
            <AlertDialogDescription>
              每个任务都从断点继续，已完成的步骤不重做。
              {failed.length > retryable.length && ` 另外 ${failed.length - retryable.length} 个的来源已经有成功的版本，不会重试。`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter className="border-t-0">
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction onClick={() => { setConfirmRetryAll(false); void runBulk("retry") }}>全部重试</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
