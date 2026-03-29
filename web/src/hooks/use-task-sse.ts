/**
 * Hook for subscribing to task SSE events with typed handlers.
 */
import { useEffect, useRef } from "react"
import { subscribeTaskEvents } from "@/lib/api"

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

interface TaskSSEHandlers {
  onStep?: (data: StepEvent) => void
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
        case "step":
          h.onStep?.(event.data as unknown as StepEvent)
          break
        case "file_ready":
          h.onFileReady?.(event.data as unknown as FileReadyEvent)
          break
        case "completed":
          h.onCompleted?.(event.data as { output_dir?: string })
          break
        case "failed":
          h.onFailed?.(event.data as { error?: string })
          break
      }
    })

    return unsub
  }, [taskId])
}
