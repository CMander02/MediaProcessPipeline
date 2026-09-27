/**
 * Says when a task finishes or fails: a toast with 打开, and a system notification while MPP is
 * in the background (browser tab hidden, or the desktop window not in front).
 */
import { useEffect } from "react"
import { toast } from "sonner"

import { useAppAccess } from "@/hooks/use-app-access-context"
import { getPreferences } from "@/hooks/use-preferences"
import { api, subscribeAllEvents } from "@/lib/api"
import { navigate } from "@/lib/router"
import { taskDisplayTitle } from "@/lib/task-display"

function openTask(taskId: string) {
  navigate(`#/result/task/${taskId}`)
}

function inBackground(): boolean {
  return document.visibilityState !== "visible" || !document.hasFocus()
}

export function systemNotificationsAvailable(): boolean {
  return typeof Notification !== "undefined"
}

function notifySystem(title: string, body: string, taskId: string) {
  if (!systemNotificationsAvailable() || Notification.permission !== "granted" || !inBackground()) return
  try {
    const notification = new Notification(title, { body, tag: taskId })
    notification.onclick = () => {
      window.focus()
      openTask(taskId)
      notification.close()
    }
  } catch {
    // Some platforms (Android WebView) only show notifications from a service worker.
  }
}

/** First line of an error, short enough for a toast. */
function shortError(error: unknown): string {
  if (typeof error !== "string") return ""
  const line = error.trim().split("\n")[0]
  return line.length > 120 ? `${line.slice(0, 119)}…` : line
}

async function announce(taskId: string, type: "completed" | "failed", data: Record<string, unknown>) {
  let title = "任务"
  try {
    const task = await api.tasks.get(taskId)
    if (task) title = taskDisplayTitle(task)
  } catch {
    // The toast still says what happened.
  }
  const action = { label: "打开", onClick: () => openTask(taskId) }
  // The result page of this task already shows it.
  const watching = window.location.hash.includes(taskId)
  if (type === "completed") {
    if (!watching) toast.success("处理完成", { description: title, action })
    notifySystem("处理完成", title, taskId)
    return
  }
  const error = shortError(data.error)
  if (!watching) toast.error("处理失败", { description: error ? `${title} · ${error}` : title, action, duration: 10_000 })
  notifySystem("处理失败", error ? `${title}\n${error}` : title, taskId)
}

export function useTaskNotifications() {
  const { online } = useAppAccess()

  useEffect(() => {
    if (!online) return
    const announced = new Set<string>()
    return subscribeAllEvents((event) => {
      if (event.type !== "completed" && event.type !== "failed") return
      const key = `${event.task_id}:${event.type}`
      if (announced.has(key) || !getPreferences().taskNotifications) return
      announced.add(key)
      void announce(event.task_id, event.type, event.data ?? {})
    })
  }, [online])
}
