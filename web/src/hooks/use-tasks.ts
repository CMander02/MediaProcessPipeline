import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { type Task, type TaskStats } from "@/lib/api"
import { useAppAccess } from "@/hooks/use-app-access-context"
import { createTaskRepository } from "@/repositories/task-repository"

export function useTasks() {
  const { online } = useAppAccess()
  const repository = useMemo(() => createTaskRepository(online), [online])
  const [tasks, setTasks] = useState<Task[]>([])
  const [stats, setStats] = useState<TaskStats>({ total: 0 })
  const [loading, setLoading] = useState(true)
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const hasActiveTasks = tasks.some((task) =>
    task.status === "pending" || task.status === "queued" || task.status === "processing" || task.status === "paused",
  )

  const refresh = useCallback(async () => {
    try {
      const snapshot = await repository.list()
      setTasks(snapshot.tasks)
      setStats(snapshot.stats)
    } catch (err) {
      console.warn("Failed to fetch tasks:", err)
    } finally {
      setLoading(false)
    }
  }, [repository])

  const scheduleRefresh = useCallback(() => {
    if (refreshTimer.current) return
    refreshTimer.current = setTimeout(() => {
      refreshTimer.current = null
      refresh()
    }, 500)
  }, [refresh])

  // SSE for real-time updates
  useEffect(() => {
    const initialRefresh = window.setTimeout(() => void refresh(), 0)
    const unsub = repository.subscribeAll(() => {
      scheduleRefresh()
    })

    let interval: ReturnType<typeof setInterval> | null = null
    if (hasActiveTasks) {
      // Fallback polling only while work is active; idle state should stay event-driven.
      interval = setInterval(refresh, 5000)
    }

    return () => {
      window.clearTimeout(initialRefresh)
      unsub()
      if (interval) clearInterval(interval)
      if (refreshTimer.current) {
        clearTimeout(refreshTimer.current)
        refreshTimer.current = null
      }
    }
  }, [hasActiveTasks, refresh, scheduleRefresh, repository])

  return { tasks, stats, loading, refresh }
}

export function useTask(taskId: string | null) {
  const { online } = useAppAccess()
  const repository = useMemo(() => createTaskRepository(online), [online])
  const [task, setTask] = useState<Task | null>(null)

  const refresh = useCallback(async () => {
    if (!taskId) return
    try {
      setTask(await repository.get(taskId))
    } catch (error) {
      console.debug("Task refresh deferred:", error)
    }
  }, [taskId, repository])

  useEffect(() => {
    const initialRefresh = window.setTimeout(() => void refresh(), 0)
    return () => window.clearTimeout(initialRefresh)
  }, [refresh])

  return { task, refresh }
}
