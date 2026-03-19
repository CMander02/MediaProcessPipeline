import { useCallback, useEffect, useState } from "react"
import { api, subscribeAllEvents, type Task, type TaskStats } from "@/lib/api"

export function useTasks() {
  const [tasks, setTasks] = useState<Task[]>([])
  const [stats, setStats] = useState<TaskStats>({ total: 0 })
  const [loading, setLoading] = useState(true)

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

  // SSE for real-time updates
  useEffect(() => {
    refresh()
    const unsub = subscribeAllEvents(() => {
      // On any event, refresh the task list
      refresh()
    })
    // Fallback polling every 5s (SSE may drop)
    const interval = setInterval(refresh, 5000)
    return () => {
      unsub()
      clearInterval(interval)
    }
  }, [refresh])

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
