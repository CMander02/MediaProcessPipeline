import type { VideoData } from "@/content/types"
import type { AnalysisResult } from "../App"

interface PipelineCallbacks {
  onStep: (step: string) => void
  onAnalysis: (analysis: NonNullable<AnalysisResult["analysis"]>) => void
  onSummary: (summary: NonNullable<AnalysisResult["summary"]>) => void
  onOutline: (outline: string) => void
}

export async function runAnalysisPipeline(
  _videoData: VideoData,
  _callbacks: PipelineCallbacks,
): Promise<void> {
  // Stub — full implementation in Task 9
  throw new Error("LLM integration not implemented yet")
}
