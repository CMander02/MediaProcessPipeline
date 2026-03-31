import type { AnalysisResult } from "../App"

export async function getCache(
  _platform: string,
  _videoId: string,
): Promise<AnalysisResult | null> {
  // Stub — full implementation in Task 10
  return null
}

export async function setCache(
  _platform: string,
  _videoId: string,
  _result: Omit<{ analysis: AnalysisResult["analysis"]; summary: AnalysisResult["summary"]; outline: AnalysisResult["outline"]; timestamp: number }, "timestamp">,
): Promise<void> {
  // Stub — full implementation in Task 10
}
