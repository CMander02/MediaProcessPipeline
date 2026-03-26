/**
 * Unified result viewer — handles both in-progress and completed archives.
 * Loads files progressively and subscribes to SSE for real-time updates.
 */
import { useCallback, useEffect, useRef, useState } from "react"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Button } from "@/components/ui/button"
import {
  ResizablePanelGroup,
  ResizablePanel,
  ResizableHandle,
} from "@/components/ui/resizable"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { DeleteConfirmDialog } from "@/components/delete-confirm-dialog"
import { useArchives, type ArchiveItem } from "@/hooks/use-archives"
import { useMediaSync } from "@/hooks/use-media-sync"
import { useViewPosition } from "@/hooks/use-view-position"
import { useTaskSSE, type FileReadyEvent, type StepEvent } from "@/hooks/use-task-sse"
import { parseSRT, subtitlesToSRT, type Subtitle } from "@/lib/srt"
import { navigate } from "@/lib/router"
import { api } from "@/lib/api"
import { PIPELINE_STEPS } from "@/lib/constants"
import { MediaPlayer } from "@/components/result/media-player"
import { SpeakerPanel } from "@/components/result/speaker-panel"
import { TranscriptTab } from "@/components/result/transcript-tab"
import { SummaryTab } from "@/components/result/summary-tab"
import { MindmapViewer } from "@/components/result/mindmap-viewer"
import { AnalysisBadges } from "@/components/result/analysis-badges"
import { ArrowLeft, Check, Copy, Download, Loader2, MoreHorizontal, Trash2, X } from "lucide-react"
import { cn } from "@/lib/utils"

interface Props {
  archivePath: string
  taskId?: string | null
}

export function ResultPageComplete({ archivePath, taskId }: Props) {
  const { archives, refresh: refreshArchives } = useArchives()
  const [archive, setArchive] = useState<ArchiveItem | null>(null)
  const [showDeleteDialog, setShowDeleteDialog] = useState(false)

  // Per-file content state (null = not yet available)
  const [summary, setSummary] = useState<string | null>(null)
  const [transcript, setTranscript] = useState<string | null>(null)
  const [isPolished, setIsPolished] = useState(false)
  const [mindmap, setMindmap] = useState<string | null>(null)
  const [analysis, setAnalysis] = useState<ArchiveItem["analysis"]>({})
  const [subtitles, setSubtitles] = useState<Subtitle[]>([])

  // Pipeline progress state
  const [taskStatus, setTaskStatus] = useState<string | null>(null)
  const [completedSteps, setCompletedSteps] = useState<string[]>([])
  const [currentStep, setCurrentStep] = useState<string | null>(null)
  const [taskError, setTaskError] = useState<string | null>(null)

  // Media URL state — may change when source/ is deleted after completion
  const [mediaUrl, setMediaUrl] = useState<string | null>(null)

  // Persist and restore viewing position
  const { updateMediaTime, updateActiveTab, getSavedPosition } = useViewPosition(archivePath)
  const savedPos = useRef(getSavedPosition())
  const [activeTab, setActiveTab] = useState(savedPos.current.activeTab || "summary")

  const { bindMedia, currentTime, duration, currentSegmentIndex, autoScroll, seekTo, onManualScroll } =
    useMediaSync({
      subtitles,
      initialTime: savedPos.current.mediaTime,
      onTimeUpdate: updateMediaTime,
    })

  const sep = archivePath.includes("\\") ? "\\" : "/"

  const handleRenameSpeaker = async (oldName: string, newName: string) => {
    const updated = subtitles.map((sub) =>
      sub.speaker === oldName ? { ...sub, speaker: newName } : sub,
    )
    setSubtitles(updated)
    const srtPath = archivePath + sep + (isPolished ? "transcript_polished.srt" : "transcript.srt")
    try {
      await api.filesystem.write(srtPath, subtitlesToSRT(updated))
    } catch (err) {
      console.warn("Failed to save SRT after speaker rename:", err)
    }
  }

  // Find archive from list
  useEffect(() => {
    const found = archives.find((a) => a.path === archivePath)
    if (found) {
      setArchive(found)
      setAnalysis(found.analysis ?? {})
      // Determine initial task status from archive
      if (found.processing) {
        setTaskStatus("processing")
      } else if (!taskId) {
        setTaskStatus("completed")
      }
    }
  }, [archives, archivePath, taskId])

  // Resolve media URL
  const resolveMediaUrl = useCallback((arch: ArchiveItem | null) => {
    if (!arch?.media_file) {
      setMediaUrl(null)
      return
    }
    if (arch.media_is_external) {
      setMediaUrl(`/api/filesystem/source-media?archive_path=${encodeURIComponent(arch.path)}`)
    } else {
      setMediaUrl(`/api/filesystem/media?path=${encodeURIComponent(arch.media_file)}`)
    }
  }, [])

  useEffect(() => {
    resolveMediaUrl(archive)
  }, [archive, resolveMediaUrl])

  // --- Progressive file loading ---
  const loadFile = useCallback(async (filename: string): Promise<string> => {
    try {
      const res = await fetch(
        `/api/filesystem/read?path=${encodeURIComponent(archivePath + sep + filename)}`,
      )
      if (!res.ok) return ""
      const data = await res.json()
      return data.content ?? ""
    } catch {
      return ""
    }
  }, [archivePath, sep])

  // Load files independently on mount
  useEffect(() => {
    // Summary
    loadFile("summary.md").then((c) => { if (c) setSummary(c) })
    // Mindmap
    loadFile("mindmap.md").then((c) => { if (c) setMindmap(c) })
    // Analysis
    loadFile("analysis.json").then((c) => {
      if (c) {
        try { setAnalysis(JSON.parse(c)) } catch { /* ignore */ }
      }
    })
    // Transcript — prefer polished, fallback to raw
    loadFile("transcript_polished.srt").then((polished) => {
      if (polished) {
        setTranscript(polished)
        setIsPolished(true)
        setSubtitles(parseSRT(polished))
      } else {
        loadFile("transcript.srt").then((raw) => {
          if (raw) {
            setTranscript(raw)
            setIsPolished(false)
            setSubtitles(parseSRT(raw))
          }
        })
      }
    })
  }, [archivePath, loadFile])

  // --- SSE subscription for in-progress tasks ---
  useTaskSSE(taskId, {
    onStep(data: StepEvent) {
      setTaskStatus("processing")
      setCurrentStep(data.step)
      if (data.completed && !completedSteps.includes(data.step)) {
        setCompletedSteps((prev) =>
          prev.includes(data.step) ? prev : [...prev, data.step],
        )
      }
    },
    onFileReady(data: FileReadyEvent) {
      const { file } = data
      if (file === "transcript_polished.srt") {
        loadFile("transcript_polished.srt").then((c) => {
          if (c) {
            setTranscript(c)
            setIsPolished(true)
            setSubtitles(parseSRT(c))
          }
        })
      } else if (file === "transcript.srt" && !isPolished) {
        loadFile("transcript.srt").then((c) => {
          if (c) {
            setTranscript(c)
            setSubtitles(parseSRT(c))
          }
        })
      } else if (file === "summary.md") {
        loadFile("summary.md").then((c) => { if (c) setSummary(c) })
      } else if (file === "mindmap.md") {
        loadFile("mindmap.md").then((c) => { if (c) setMindmap(c) })
      } else if (file === "analysis.json") {
        loadFile("analysis.json").then((c) => {
          if (c) {
            try { setAnalysis(JSON.parse(c)) } catch { /* ignore */ }
          }
        })
      } else if (file === "metadata.json") {
        // Refresh archive list to pick up updated metadata
        refreshArchives()
      }
    },
    onCompleted() {
      setTaskStatus("completed")
      // Refresh archive list — media URL may have changed (source/ deleted)
      refreshArchives()
    },
    onFailed(data) {
      setTaskStatus("failed")
      setTaskError(data.error ?? "处理失败")
    },
  })

  const mediaType = archive?.has_video ? "video" : "audio"
  const displayTitle = archive?.title ?? archivePath.split(/[/\\]/).pop()
  const isProcessing = taskStatus === "processing" || taskStatus === "queued"
  const hasContent = summary || transcript || mindmap

  const [copied, setCopied] = useState(false)

  const getTabContent = () => {
    if (activeTab === "summary") return { content: summary, suffix: "摘要", ext: "md" }
    if (activeTab === "transcript") return { content: transcript, suffix: "字幕", ext: "srt" }
    if (activeTab === "mindmap") return { content: mindmap, suffix: "导图", ext: "md" }
    return null
  }

  const handleCopy = async () => {
    const tab = getTabContent()
    if (!tab?.content) return
    await navigator.clipboard.writeText(tab.content)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const handleDownload = () => {
    const tab = getTabContent()
    if (!tab?.content) return
    const baseName = (displayTitle ?? "output").replace(/[/\\:*?"<>|]/g, "_")
    const filename = `${baseName}-${tab.suffix}.${tab.ext}`
    const blob = new Blob([tab.content], { type: "text/plain;charset=utf-8" })
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = filename
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="flex h-full flex-col">
      {/* Top bar */}
      <div className="shrink-0 flex items-center gap-3 px-4 py-2 border-b">
        <Button variant="ghost" size="sm" onClick={() => navigate("#/files")}>
          <ArrowLeft className="h-4 w-4 mr-1" />
          返回
        </Button>
        <h2 className="text-sm font-medium truncate flex-1">
          {displayTitle}
        </h2>
        {isProcessing && (
          <span className="text-xs text-blue-600 flex items-center gap-1">
            <Loader2 className="h-3 w-3 animate-spin" />
            处理中
          </span>
        )}
        <DropdownMenu>
          <DropdownMenuTrigger
            render={<Button variant="ghost" size="icon-sm" />}
          >
            <MoreHorizontal className="h-4 w-4" />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem
              variant="destructive"
              onClick={() => setShowDeleteDialog(true)}
            >
              <Trash2 className="h-4 w-4" />
              删除
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {/* Pipeline progress bar (visible when processing) */}
      {isProcessing && (
        <div className="shrink-0 flex items-center gap-1 px-4 py-1.5 border-b bg-muted/30">
          {PIPELINE_STEPS.map((step, i) => {
            const isDone = completedSteps.includes(step.id)
            const isCurrent = currentStep === step.id
            return (
              <div key={step.id} className="flex items-center gap-1">
                <div className="flex items-center gap-1">
                  <div
                    className={cn(
                      "flex h-4 w-4 shrink-0 items-center justify-center rounded-full border transition-colors",
                      isDone && "border-emerald-500 bg-emerald-500 text-white",
                      isCurrent && !isDone && "border-blue-500 bg-blue-50 dark:bg-blue-950",
                      !isDone && !isCurrent && "border-muted-foreground/30",
                    )}
                  >
                    {isDone ? (
                      <Check className="h-2.5 w-2.5" />
                    ) : isCurrent ? (
                      <Loader2 className="h-2.5 w-2.5 animate-spin text-blue-600" />
                    ) : (
                      <span className="h-1 w-1 rounded-full bg-muted-foreground/20" />
                    )}
                  </div>
                  <span
                    className={cn(
                      "text-[10px] whitespace-nowrap",
                      isDone && "text-emerald-700 dark:text-emerald-400",
                      isCurrent && "text-blue-700 dark:text-blue-400",
                      !isDone && !isCurrent && "text-muted-foreground",
                    )}
                  >
                    {step.name}
                  </span>
                </div>
                {i < PIPELINE_STEPS.length - 1 && (
                  <div
                    className={cn(
                      "h-px w-4 mx-0.5",
                      isDone ? "bg-emerald-400" : "bg-border",
                    )}
                  />
                )}
              </div>
            )
          })}
        </div>
      )}

      {/* Error display */}
      {taskStatus === "failed" && taskError && (
        <div className="shrink-0 mx-4 mt-2 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
          {taskError}
        </div>
      )}

      {/* Main content area */}
      <div className="flex-1 min-h-0 relative">
        <ResizablePanelGroup
          orientation="horizontal"
          className="absolute inset-0"
        >
          {/* Left panel — media + info */}
          <ResizablePanel defaultSize="50%" minSize="20%" maxSize="60%">
            <div className="h-full overflow-y-auto p-4 space-y-3">
              {mediaUrl ? (
                <div className="sticky top-0 z-10 bg-background pb-2">
                  <MediaPlayer
                    src={mediaUrl}
                    type={mediaType}
                    bindMedia={bindMedia}
                    subtitleSrt={transcript ?? undefined}
                  />
                </div>
              ) : isProcessing ? (
                <div className="flex items-center justify-center h-40 rounded-lg bg-muted/50">
                  <div className="text-center text-muted-foreground">
                    <Loader2 className="h-6 w-6 animate-spin mx-auto mb-2" />
                    <p className="text-xs">正在下载媒体...</p>
                  </div>
                </div>
              ) : null}
              {subtitles.length > 0 && (
                <SpeakerPanel
                  subtitles={subtitles}
                  duration={duration}
                  currentTime={currentTime}
                  onSeek={seekTo}
                  onRenameSpeaker={handleRenameSpeaker}
                />
              )}
              <AnalysisBadges analysis={analysis} />
            </div>
          </ResizablePanel>

          <ResizableHandle withHandle />

          {/* Right panel — tabbed content */}
          <ResizablePanel defaultSize="50%" minSize="25%">
            <div className="h-full flex flex-col p-4">
              <Tabs value={activeTab} onValueChange={(v: any) => { setActiveTab(String(v)); updateActiveTab(String(v)) }} className="flex flex-col flex-1 min-h-0">
                <div className="shrink-0 flex items-center gap-2">
                  <TabsList>
                    <TabsTrigger value="summary">摘要</TabsTrigger>
                    <TabsTrigger value="transcript">
                      字幕
                      {transcript && !isPolished && isProcessing && (
                        <span className="ml-1 text-[10px] text-amber-600">(原始)</span>
                      )}
                    </TabsTrigger>
                    {(mindmap || isProcessing) && <TabsTrigger value="mindmap">导图</TabsTrigger>}
                  </TabsList>
                  <div className="flex-1" />
                  {getTabContent()?.content && (
                    <>
                      <button
                        onClick={handleCopy}
                        className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                        title="复制全部内容"
                      >
                        {copied ? <Check className="h-3.5 w-3.5 text-emerald-500" /> : <Copy className="h-3.5 w-3.5" />}
                      </button>
                      <button
                        onClick={handleDownload}
                        className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                        title="下载为文件"
                      >
                        <Download className="h-3.5 w-3.5" />
                      </button>
                    </>
                  )}
                </div>

                <TabsContent value="summary" className="mt-3 relative flex-1">
                  <div className="absolute inset-0 rounded-md border">
                    {summary ? (
                      <SummaryTab content={summary} />
                    ) : isProcessing ? (
                      <div className="flex items-center justify-center h-full text-muted-foreground">
                        <Loader2 className="h-4 w-4 animate-spin mr-2" />
                        <span className="text-sm">等待分析完成...</span>
                      </div>
                    ) : (
                      <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
                        暂无摘要
                      </div>
                    )}
                  </div>
                </TabsContent>

                <TabsContent value="transcript" className="mt-3 relative flex-1">
                  <div className="absolute inset-0 rounded-md border flex flex-col">
                    {subtitles.length > 0 ? (
                      <TranscriptTab
                        subtitles={subtitles}
                        currentSegmentIndex={currentSegmentIndex}
                        autoScroll={autoScroll}
                        onSegmentClick={(sub) => seekTo(sub.startTime)}
                        onManualScroll={onManualScroll}
                        srtPath={archivePath + sep + (isPolished ? "transcript_polished.srt" : "transcript.srt")}
                        onSubtitlesChange={setSubtitles}
                      />
                    ) : isProcessing ? (
                      <div className="flex items-center justify-center h-full text-muted-foreground">
                        <Loader2 className="h-4 w-4 animate-spin mr-2" />
                        <span className="text-sm">等待转录完成...</span>
                      </div>
                    ) : (
                      <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
                        无字幕数据
                      </div>
                    )}
                  </div>
                </TabsContent>

                {(mindmap || isProcessing) && (
                  <TabsContent value="mindmap" className="mt-3 relative flex-1">
                    {mindmap ? (
                      <MindmapViewer markdown={mindmap} fillContainer />
                    ) : isProcessing ? (
                      <div className="flex items-center justify-center h-full text-muted-foreground">
                        <Loader2 className="h-4 w-4 animate-spin mr-2" />
                        <span className="text-sm">等待分析完成...</span>
                      </div>
                    ) : null}
                  </TabsContent>
                )}
              </Tabs>
            </div>
          </ResizablePanel>
        </ResizablePanelGroup>
      </div>

      {/* Delete confirmation dialog */}
      <DeleteConfirmDialog
        open={showDeleteDialog}
        onOpenChange={setShowDeleteDialog}
        title={displayTitle ?? ""}
        archivePath={archivePath}
        onDeleted={() => navigate("#/files")}
      />
    </div>
  )
}
