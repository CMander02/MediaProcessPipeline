import { ref, computed, onMounted, onUnmounted, watch } from "vue"
import type { Task, TaskCreate, TaskStatus } from "@/types"
import { tasksApi } from "@/api"

export function useTasks(pollInterval = 200) {
  const tasks = ref<Task[]>([])
  const loading = ref(true)
  const error = ref<string | null>(null)

  let pollTimer: ReturnType<typeof setInterval> | null = null

  const fetchTasks = async (status?: TaskStatus) => {
    try {
      const data = await tasksApi.list(status)
      tasks.value = data
      error.value = null
    } catch (e) {
      console.warn("Failed to fetch tasks:", e)
      error.value = e instanceof Error ? e.message : "Failed to fetch tasks"
    } finally {
      loading.value = false
    }
  }

  const createTask = async (data: TaskCreate) => {
    const task = await tasksApi.create(data)
    tasks.value = [task, ...tasks.value]
    // Immediately start polling after task creation
    startPolling()
    // Fetch updated status after a short delay (backend starts processing immediately)
    setTimeout(() => fetchTasks(), 300)
    return task
  }

  const cancelTask = async (id: string) => {
    await tasksApi.cancel(id)
    tasks.value = tasks.value.map((t) =>
      t.id === id ? { ...t, status: "cancelled" as TaskStatus } : t
    )
  }

  const hasActiveTasks = computed(() =>
    tasks.value.some((t) => t.status === "queued" || t.status === "processing")
  )

  // Start/stop polling based on active tasks
  const startPolling = () => {
    if (pollTimer) return
    pollTimer = setInterval(() => {
      if (hasActiveTasks.value) {
        fetchTasks()
      }
    }, pollInterval)
  }

  const stopPolling = () => {
    if (pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  watch(hasActiveTasks, (active) => {
    if (active) {
      startPolling()
    } else {
      stopPolling()
    }
  })

  onMounted(() => {
    setTimeout(() => fetchTasks(), 100)
    if (hasActiveTasks.value) {
      startPolling()
    }
  })

  onUnmounted(() => {
    stopPolling()
  })

  return {
    tasks,
    loading,
    error,
    refresh: fetchTasks,
    createTask,
    cancelTask,
  }
}

export function useTask(taskId: () => string | null, pollInterval = 2000) {
  const task = ref<Task | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  let pollTimer: ReturnType<typeof setInterval> | null = null

  const fetchTask = async () => {
    const id = taskId()
    if (!id) return

    loading.value = true
    try {
      const data = await tasksApi.get(id)
      task.value = data
      error.value = null
    } catch (e) {
      console.warn("Failed to fetch task:", e)
      error.value = e instanceof Error ? e.message : "Failed to fetch task"
    } finally {
      loading.value = false
    }
  }

  const isActive = computed(() =>
    task.value && ["queued", "processing"].includes(task.value.status)
  )

  const startPolling = () => {
    if (pollTimer) return
    pollTimer = setInterval(() => {
      if (isActive.value) {
        fetchTask()
      }
    }, pollInterval)
  }

  const stopPolling = () => {
    if (pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  watch(isActive, (active) => {
    if (active) {
      startPolling()
    } else {
      stopPolling()
    }
  })

  watch(taskId, (newId) => {
    if (newId) {
      fetchTask()
    } else {
      task.value = null
    }
  }, { immediate: true })

  onUnmounted(() => {
    stopPolling()
  })

  return { task, loading, error, refresh: fetchTask }
}
