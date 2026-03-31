import type { VideoData } from "@/content/types"
import type { AnalysisResult } from "../App"
import { exportToLocal } from "../lib/export"

interface ActionBarProps {
  canAnalyze: boolean
  canExport: boolean
  analyzing: boolean
  analysisStep: string
  videoData: VideoData | null
  result: AnalysisResult
  onAnalyze: () => void
  onReanalyze: () => void
  onSettings: () => void
}

export function ActionBar({
  canAnalyze, canExport, analyzing, analysisStep,
  videoData, result, onAnalyze, onReanalyze, onSettings,
}: ActionBarProps) {
  const handleExport = async () => {
    if (!videoData) return
    try {
      await exportToLocal(videoData, result)
    } catch (e) {
      console.error("[MPP] Export failed:", e)
    }
  }

  return (
    <div className="flex items-center gap-2 border-b px-3 py-2">
      {analyzing ? (
        <div className="flex flex-1 items-center gap-2 text-xs text-gray-500">
          <svg className="h-3.5 w-3.5 animate-spin" viewBox="0 0 24 24" fill="none">
            <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" className="opacity-20" />
            <path d="M12 2a10 10 0 0 1 10 10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
          </svg>
          {analysisStep}
        </div>
      ) : (
        <>
          <button
            onClick={canExport ? onReanalyze : onAnalyze}
            disabled={!canAnalyze}
            className="flex items-center gap-1.5 rounded-md bg-blue-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-blue-700 disabled:opacity-40"
          >
            {canExport ? "Re-analyze" : "Analyze"}
          </button>
          <button
            onClick={handleExport}
            disabled={!canExport}
            className="rounded-md border px-3 py-1.5 text-xs transition-colors hover:bg-gray-50 disabled:opacity-40"
          >
            Export
          </button>
        </>
      )}
      <button
        onClick={onSettings}
        className="ml-auto rounded-md p-1.5 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600"
        title="Settings"
      >
        <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
          <path strokeLinecap="round" strokeLinejoin="round" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
          <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
        </svg>
      </button>
    </div>
  )
}
