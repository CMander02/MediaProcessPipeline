/**
 * One shared view of queued / running / paused tasks.
 *
 * Library cards, the activity panel and the backend page all read from here, so there is a
 * single poll loop (plus SSE nudges) no matter how many components are mounted.
 */
import { useSyncExternalStore } from "react"

import { api, subscribeAllEvents, type Task } from "@/lib/api"

interface ActiveTasksSnapshot {
  tasks: Task[]
  loaded: boolean
}

let snapshot: ActiveTasksSnapshot = { tasks: [], loaded: false }
const listeners = new Set<() => void>()
let pollTimer: number | null = null
let nudgeTimer: number | null = null
let stopEvents: (() => void) | null = null
let inFlight: Promise<void> | null = null

function emit() {
  listeners.forEach((listener) => listener())
}

function schedulePoll() {
  if (pollTimer !== null) window.clearTimeout(pollTimer)
  if (listeners.size === 0) return
  // Nothing moves while every task is paused: poll slowly and let SSE wake us up.
  const running = snapshot.tasks.some((task) => task.status === "processing" || task.status === "queued")
  pollTimer = window.setTimeout(() => void refreshActiveTasks(), running ? 4000 : 20000)
}

async function loadActiveTasks(): Promise<Task[]> {
  const [processing, queued, paused] = await Promise.all([
    api.tasks.list("processing", 200),
    api.tasks.list("queued", 200),
    api.tasks.list("paused", 200),
  ])
  return [...processing, ...queued, ...paused]
}

export function refreshActiveTasks(): Promise<void> {
  if (inFlight) return inFlight
  inFlight = loadActiveTasks()
    .then((tasks) => {
      snapshot = { tasks, loaded: true }
      emit()
    })
    .catch(() => {
      // Offline or daemon restarting: keep the last known list.
    })
    .finally(() => {
      inFlight = null
      schedulePoll()
    })
  return inFlight
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  if (listeners.size === 1) {
    void refreshActiveTasks()
    stopEvents = subscribeAllEvents(() => {
      if (nudgeTimer !== null) return
      nudgeTimer = window.setTimeout(() => {
        nudgeTimer = null
        void refreshActiveTasks()
      }, 500)
    })
  }
  return () => {
    listeners.delete(listener)
    if (listeners.size === 0) {
      stopEvents?.()
      stopEvents = null
      if (pollTimer !== null) window.clearTimeout(pollTimer)
      pollTimer = null
    }
  }
}

function getSnapshot() {
  return snapshot
}

export function useActiveTasks(): ActiveTasksSnapshot {
  return useSyncExternalStore(subscribe, getSnapshot)
}
