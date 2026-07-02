/**
 * Hook for subscribing to task SSE events with typed handlers.
 */
import { useEffect, useRef } from "react"
import { subscribeTaskEvents, type TaskFlowSnapshot, type TaskTimelineEvent } from "@/lib/api"

export interface FileReadyEvent {
  file: string
  path: string
}

export interface StepEvent {
  step: string
  completed: boolean
  progress: number
  message: string
}

export interface FlowEvent {
  stage?: string
  step_id?: string
  completed?: boolean
  level?: string
  message?: string
  flow?: TaskFlowSnapshot
  platform?: string
  content_subtype?: string
  [key: string]: unknown
}

export interface SnapshotEvent {
  status: string
  progress: number
  message: string
  current_step: string | null
  completed_steps: string[]
  flow?: TaskFlowSnapshot | null
  error: string | null
}

interface TaskSSEHandlers {
  onSnapshot?: (data: SnapshotEvent) => void
  onStep?: (data: StepEvent) => void
  onFlow?: (data: FlowEvent) => void
  onTimeline?: (event: TaskTimelineEvent) => void
  onFileReady?: (data: FileReadyEvent) => void
  onCompleted?: (data: { output_dir?: string }) => void
  onFailed?: (data: { error?: string }) => void
}

export function useTaskSSE(
  taskId: string | null | undefined,
  handlers: TaskSSEHandlers,
) {
  const handlersRef = useRef(handlers)
  handlersRef.current = handlers

  useEffect(() => {
    if (!taskId) return

    const unsub = subscribeTaskEvents(taskId, (event) => {
      const h = handlersRef.current
      switch (event.type) {
        case "snapshot":
          h.onSnapshot?.(event.data as unknown as SnapshotEvent)
          break
        case "step":
          h.onStep?.(event.data as unknown as StepEvent)
          break
        case "flow_selected":
        case "flow_step":
        case "warning":
        case "diagnostic":
        case "substep":
          h.onFlow?.(event.data as FlowEvent)
          h.onTimeline?.(toTimelineEvent(event))
          break
        case "file_ready":
          h.onFileReady?.(event.data as unknown as FileReadyEvent)
          break
        case "completed":
          h.onCompleted?.(event.data as { output_dir?: string })
          break
        case "failed":
          h.onFailed?.(event.data as { error?: string })
          h.onTimeline?.(toTimelineEvent(event))
          break
        default:
          if (event.type.includes(".")) {
            h.onTimeline?.(toTimelineEvent(event))
          }
      }
    })

    return unsub
  }, [taskId])
}

function toTimelineEvent(event: {
  task_id: string
  type: string
  data: Record<string, unknown>
  timestamp: string
}): TaskTimelineEvent {
  const data = event.data
  return {
    id: Number(data.id ?? Date.parse(event.timestamp) ?? 0),
    task_id: event.task_id,
    event_type: event.type,
    stage: typeof data.stage === "string" ? data.stage : null,
    step_id: typeof data.step_id === "string" ? data.step_id : null,
    level: typeof data.level === "string" ? data.level : event.type === "failed" ? "error" : "info",
    message: typeof data.message === "string" ? data.message : typeof data.error === "string" ? data.error : null,
    data,
    timestamp: event.timestamp,
  }
}
