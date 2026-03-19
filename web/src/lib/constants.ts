export const PIPELINE_STEPS = [
  { id: "download", name: "下载媒体" },
  { id: "separate", name: "分离人声" },
  { id: "transcribe", name: "转录音频" },
  { id: "analyze", name: "分析+摘要" },
  { id: "polish", name: "润色字幕" },
  { id: "archive", name: "归档保存" },
] as const

export const STEP_NAME: Record<string, string> = Object.fromEntries(
  PIPELINE_STEPS.map((s) => [s.id, s.name]),
)

export const STATUS_CONFIG: Record<string, { label: string; color: string; dot: string }> = {
  queued: { label: "排队中", color: "text-amber-600", dot: "bg-amber-500" },
  processing: { label: "处理中", color: "text-blue-600", dot: "bg-blue-500" },
  completed: { label: "已完成", color: "text-emerald-600", dot: "bg-emerald-500" },
  failed: { label: "失败", color: "text-red-600", dot: "bg-red-500" },
  cancelled: { label: "已取消", color: "text-muted-foreground", dot: "bg-muted-foreground" },
}
