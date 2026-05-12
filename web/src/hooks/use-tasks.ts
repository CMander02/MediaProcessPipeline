import { useCallback, useEffect, useRef, useState } from "react"
import { api, subscribeAllEvents, type Task, type TaskStats } from "@/lib/api"

export function useTasks() {
  const [tasks, setTasks] = useState<Task[]>([])
  const [stats, setStats] = useState<TaskStats>({ total: 0 })
  const [loading, setLoading] = useState(true)
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const hasActiveTasks = tasks.some((task) =>
    task.status === "pending" || task.status === "queued" || task.status === "processing",
  )

  const refresh = useCallback(async () => {
    try {
      const [taskList, taskStats] = await Promise.all([
        api.tasks.list(undefined, 50),
        api.tasks.stats(),
      ])
      setTasks(taskList)
      setStats(taskStats)
    } catch (err) {
      console.warn("Failed to fetch tasks:", err)
    } finally {
      setLoading(false)
    }
  }, [])

  const scheduleRefresh = useCallback(() => {
    if (refreshTimer.current) return
    refreshTimer.current = setTimeout(() => {
      refreshTimer.current = null
      refresh()
    }, 500)
  }, [refresh])

  // SSE for real-time updates
  useEffect(() => {
    refresh()
    const unsub = subscribeAllEvents(() => {
      scheduleRefresh()
    })

    let interval: ReturnType<typeof setInterval> | null = null
    if (hasActiveTasks) {
      // Fallback polling only while work is active; idle state should stay event-driven.
      interval = setInterval(refresh, 5000)
    }

    return () => {
      unsub()
      if (interval) clearInterval(interval)
      if (refreshTimer.current) {
        clearTimeout(refreshTimer.current)
        refreshTimer.current = null
      }
    }
  }, [hasActiveTasks, refresh, scheduleRefresh])

  return { tasks, stats, loading, refresh }
}

export function useTask(taskId: string | null) {
  const [task, setTask] = useState<Task | null>(null)

  const refresh = useCallback(async () => {
    if (!taskId) return
    try {
      setTask(await api.tasks.get(taskId))
    } catch {}
  }, [taskId])

  useEffect(() => {
    refresh()
  }, [refresh])

  return { task, refresh }
}
