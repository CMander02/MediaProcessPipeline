import type { VideoData } from "@/content/types"
import type { AnalysisResult } from "../App"
import { loadSettings, type LLMSettings } from "../components/SettingsView"
import { getAnalyzePrompt, getSummarizePrompt, getOutlinePrompt } from "./prompts"
import { setCache } from "./storage"

interface ChatResponse {
  choices: Array<{
    message: { content: string }
  }>
}

function getApiUrl(settings: LLMSettings): string {
  if (settings.provider === "anthropic") {
    return (settings.apiBase || "https://api.anthropic.com/v1") + "/chat/completions"
  }
  if (settings.provider === "deepseek") {
    return (settings.apiBase || "https://api.deepseek.com/v1") + "/chat/completions"
  }
  if (settings.provider === "custom") {
    return settings.apiBase + "/chat/completions"
  }
  // openai
  return (settings.apiBase || "https://api.openai.com/v1") + "/chat/completions"
}

async function callLLM(prompt: string, settings: LLMSettings, maxRetries = 3): Promise<string> {
  const url = getApiUrl(settings)

  for (let attempt = 0; attempt < maxRetries; attempt++) {
    try {
      const resp = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${settings.apiKey}`,
        },
        body: JSON.stringify({
          model: settings.model,
          messages: [{ role: "user", content: prompt }],
          temperature: settings.temperature,
        }),
      })

      if (!resp.ok) {
        const body = await resp.text()
        throw new Error(`API error ${resp.status}: ${body.slice(0, 200)}`)
      }

      const data: ChatResponse = await resp.json()
      return data.choices[0]?.message?.content || ""
    } catch (e) {
      const isRetryable =
        e instanceof TypeError || // network error
        (e instanceof Error && e.message.includes("timeout"))
      if (isRetryable && attempt < maxRetries - 1) {
        await new Promise((r) => setTimeout(r, 2 ** attempt * 1000))
        continue
      }
      throw e
    }
  }
  throw new Error("Max retries exceeded")
}

function parseJSON(text: string): Record<string, unknown> {
  const start = text.indexOf("{")
  const end = text.lastIndexOf("}") + 1
  if (start >= 0 && end > start) {
    return JSON.parse(text.slice(start, end))
  }
  throw new Error("No JSON found in response")
}

interface PipelineCallbacks {
  onStep: (step: string) => void
  onAnalysis: (analysis: AnalysisResult["analysis"]) => void
  onSummary: (summary: AnalysisResult["summary"]) => void
  onOutline: (outline: string) => void
}

export async function runAnalysisPipeline(
  videoData: VideoData,
  callbacks: PipelineCallbacks,
): Promise<void> {
  const settings = await loadSettings()
  if (!settings.apiKey) {
    throw new Error("Please configure your LLM API key in Settings")
  }

  const subtitleText = videoData.subtitles.map((s) => s.text).join("\n")
  const truncatedText = subtitleText.slice(0, 8000)

  // Step 1: Analyze
  callbacks.onStep("Analyzing content...")
  const analyzePrompt = getAnalyzePrompt(
    videoData.title,
    truncatedText,
    videoData.uploader,
    videoData.description,
  )
  const analyzeResp = await callLLM(analyzePrompt, settings)
  const analysis = parseJSON(analyzeResp) as AnalysisResult["analysis"]
  callbacks.onAnalysis(analysis)

  // Step 2: Summarize
  callbacks.onStep("Generating summary...")
  const summarizePrompt = getSummarizePrompt(subtitleText)
  const summarizeResp = await callLLM(summarizePrompt, settings)
  const summary = parseJSON(summarizeResp) as AnalysisResult["summary"]
  callbacks.onSummary(summary)

  // Step 3: Outline
  callbacks.onStep("Generating outline...")
  const outlinePrompt = getOutlinePrompt(subtitleText)
  const outline = await callLLM(outlinePrompt, settings)
  callbacks.onOutline(outline)

  // Cache result
  await setCache(videoData.platform, videoData.videoId, {
    analysis,
    summary,
    outline,
  })
}
