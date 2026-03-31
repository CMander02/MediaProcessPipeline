import { useEffect, useState, useCallback } from "react"
import type { VideoData, ContentMessage } from "@/content/types"
import { VideoInfo } from "./components/VideoInfo"
import { ActionBar } from "./components/ActionBar"
import { SummaryView } from "./components/SummaryView"
import { OutlineView } from "./components/OutlineView"
import { SubtitleView } from "./components/SubtitleView"
import { SettingsView } from "./components/SettingsView"
import { getCache } from "./lib/storage"

export interface AnalysisResult {
  analysis: { language: string; content_type: string; keywords: string[]; proper_nouns: string[] } | null
  summary: { tldr: string; key_facts: string[] } | null
  outline: string | null
}

type ViewState = "empty" | "no-subtitles" | "ready" | "analyzing" | "done" | "error"

export function App() {
  const [videoData, setVideoData] = useState<VideoData | null>(null)
  const [viewState, setViewState] = useState<ViewState>("empty")
  const [result, setResult] = useState<AnalysisResult>({ analysis: null, summary: null, outline: null })
  const [analysisStep, setAnalysisStep] = useState<string>("")
  const [error, setError] = useState<string>("")
  const [showSettings, setShowSettings] = useState(false)
  const [noSubTitle, setNoSubTitle] = useState("")

  useEffect(() => {
    const handler = (message: ContentMessage) => {
      if (message.type === "VIDEO_DATA") {
        setVideoData(message.data)
        setViewState("ready")
        setResult({ analysis: null, summary: null, outline: null })
        setError("")
        getCache(message.data.platform, message.data.videoId).then((cached) => {
          if (cached) {
            setResult(cached)
            setViewState("done")
          }
        })
      } else if (message.type === "NO_SUBTITLES") {
        setVideoData(null)
        setNoSubTitle(message.title)
        setViewState("no-subtitles")
      } else if (message.type === "VIDEO_CHANGED") {
        setVideoData(null)
        setViewState("empty")
        setResult({ analysis: null, summary: null, outline: null })
      }
    }
    chrome.runtime.onMessage.addListener(handler)
    return () => chrome.runtime.onMessage.removeListener(handler)
  }, [])

  const handleAnalyze = useCallback(async (force?: boolean) => {
    if (!videoData) return

    if (!force) {
      const cached = await getCache(videoData.platform, videoData.videoId)
      if (cached) {
        setResult(cached)
        setViewState("done")
        return
      }
    }

    setViewState("analyzing")
    setError("")

    try {
      const { runAnalysisPipeline } = await import("./lib/llm")
      await runAnalysisPipeline(videoData, {
        onStep: (step) => setAnalysisStep(step),
        onAnalysis: (analysis) => setResult((r) => ({ ...r, analysis })),
        onSummary: (summary) => setResult((r) => ({ ...r, summary })),
        onOutline: (outline) => setResult((r) => ({ ...r, outline })),
      })
      setViewState("done")
    } catch (e) {
      setError(e instanceof Error ? e.message : "Analysis failed")
      setViewState("error")
    }
  }, [videoData])

  if (showSettings) {
    return <SettingsView onBack={() => setShowSettings(false)} />
  }

  if (viewState === "empty") {
    return (
      <div className="flex h-screen items-center justify-center p-6 text-center text-sm text-gray-400">
        Navigate to a Bilibili or YouTube video to get started
      </div>
    )
  }

  if (viewState === "no-subtitles") {
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-2 p-6 text-center">
        <p className="text-sm font-medium">{noSubTitle}</p>
        <p className="text-sm text-gray-400">No subtitles available for this video</p>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-0">
      {videoData && <VideoInfo data={videoData} />}
      <ActionBar
        canAnalyze={viewState === "ready" || viewState === "done" || viewState === "error"}
        canExport={viewState === "done"}
        analyzing={viewState === "analyzing"}
        analysisStep={analysisStep}
        videoData={videoData}
        result={result}
        onAnalyze={() => handleAnalyze(false)}
        onReanalyze={() => handleAnalyze(true)}
        onSettings={() => setShowSettings(true)}
      />
      {error && (
        <div className="border-b border-red-200 bg-red-50 px-4 py-2 text-xs text-red-600">
          {error}
        </div>
      )}
      <SummaryView summary={result.summary} />
      <OutlineView outline={result.outline} />
      <SubtitleView subtitles={videoData?.subtitles ?? []} />
    </div>
  )
}
