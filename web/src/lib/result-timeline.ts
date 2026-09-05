import { type TaskTimelineEvent } from "@/lib/api"

export function timelineEventKey(event: TaskTimelineEvent): string {
  return `${event.id}:${event.event_type}:${event.timestamp}`
}

export function timelineTime(timestamp: string): string {
  return timestamp.split("T")[1]?.slice(0, 8) ?? ""
}

function timelineMessage(event: TaskTimelineEvent): string {
  if (event.message) return event.message
  if (typeof event.data.error === "string") return event.data.error
  if (typeof event.data.reason === "string") return event.data.reason
  return event.event_type
}

export function timelineStatusText(event: TaskTimelineEvent, stepLabels: Record<string, string> = {}): string {
  if (event.event_type === "queued") return "任务已进入队列"
  if (event.event_type === "processing") return "开始处理任务"
  if (event.event_type === "completed") return "处理完成"
  if (event.event_type === "failed") return timelineMessage(event)
  const stepLabel = (event.step_id && stepLabels[event.step_id]) || (event.stage && stepLabels[event.stage])
  const message = timelineMessage(event)
  if (message && message !== event.stage && message !== event.step_id) return message
  return stepLabel ?? message
}

export function timelineStatusClass(level: string): string {
  if (level === "error") return "border-destructive/40 bg-destructive/5 text-destructive"
  if (level === "warning") return "border-border bg-muted/50 text-foreground"
  return "border-border bg-muted/40 text-muted-foreground"
}
