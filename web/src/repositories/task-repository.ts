import { api, subscribeAllEvents, subscribeTaskEvents, type Task, type TaskStats } from "@/lib/api"

export interface TaskRepository {
  list(): Promise<{ tasks: Task[]; stats: TaskStats }>
  get(taskId: string): Promise<Task | null>
  subscribeAll(listener: () => void): () => void
  subscribe(taskId: string, listener: Parameters<typeof subscribeTaskEvents>[1]): () => void
}

export function createTaskRepository(online: boolean): TaskRepository {
  if (!online) {
    return {
      async list() { return { tasks: [], stats: { total: 0 } } },
      async get() { return null },
      subscribeAll() { return () => {} },
      subscribe() { return () => {} },
    }
  }
  return {
    async list() {
      const [tasks, stats] = await Promise.all([api.tasks.list(undefined, 50), api.tasks.stats()])
      return { tasks, stats }
    },
    async get(taskId) { return api.tasks.get(taskId) },
    subscribeAll(listener) { return subscribeAllEvents(listener) },
    subscribe(taskId, listener) { return subscribeTaskEvents(taskId, listener) },
  }
}
