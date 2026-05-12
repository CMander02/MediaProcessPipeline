import { useEffect, useState } from "react"
import { api, type PipelineStep } from "@/lib/api"

export const PIPELINE_STEPS_FALLBACK: PipelineStep[] = [
  { id: "download", name: "下载媒体", name_en: "Downloading" },
  { id: "separate", name: "分离人声", name_en: "Separating vocals" },
  { id: "transcribe", name: "转录音频", name_en: "Transcribing" },
  { id: "voiceprint", name: "声纹识别", name_en: "Matching voiceprints" },
  { id: "polish", name: "润色字幕", name_en: "Polishing transcript" },
  { id: "analyze", name: "分析+摘要+脑图", name_en: "Analyzing & summarizing" },
  { id: "archive", name: "归档保存", name_en: "Archiving" },
]

export const STEP_NAME: Record<string, string> = Object.fromEntries(
  PIPELINE_STEPS_FALLBACK.map((s) => [s.id, s.name]),
)

let cachedPipelineSteps: PipelineStep[] = [...PIPELINE_STEPS_FALLBACK]

export function usePipelineSteps() {
  const [steps, setSteps] = useState<PipelineStep[]>(cachedPipelineSteps)

  useEffect(() => {
    let cancelled = false
    api.pipeline.steps()
      .then((data) => {
        if (!cancelled && data.steps?.length) {
          cachedPipelineSteps = data.steps
          setSteps(data.steps)
        }
      })
      .catch(() => {
        if (!cancelled) setSteps(cachedPipelineSteps)
      })
    return () => {
      cancelled = true
    }
  }, [])

  return steps
}

export const STATUS_CONFIG: Record<string, { label: string; color: string; dot: string }> = {
  queued: { label: "排队中", color: "text-amber-600", dot: "bg-amber-500" },
  processing: { label: "处理中", color: "text-blue-600", dot: "bg-blue-500" },
  completed: { label: "已完成", color: "text-emerald-600", dot: "bg-emerald-500" },
  failed: { label: "失败", color: "text-red-600", dot: "bg-red-500" },
  cancelled: { label: "已取消", color: "text-muted-foreground", dot: "bg-muted-foreground" },
}
