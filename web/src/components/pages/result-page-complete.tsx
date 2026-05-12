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
import { SpeakerMergeDialog, type SpeakerMergeInfo } from "@/components/speaker-merge-dialog"
import { useArchives, type ArchiveItem } from "@/hooks/use-archives"
import { useMediaSync } from "@/hooks/use-media-sync"
import { useViewPosition } from "@/hooks/use-view-position"
import { useTaskSSE, type FileReadyEvent, type StepEvent } from "@/hooks/use-task-sse"
import { parseSRT, subtitlesToSRT, type Subtitle } from "@/lib/srt"
import { navigate } from "@/lib/router"
import { api } from "@/lib/api"
import { usePipelineSteps } from "@/lib/constants"
import { MediaPlayer } from "@/components/result/media-player"
import { SpeakerPanel } from "@/components/result/speaker-panel"
import { TranscriptTab } from "@/components/result/transcript-tab"
import { SummaryTab } from "@/components/result/summary-tab"
import { MindmapViewer } from "@/components/result/mindmap-viewer"
import { HugeiconsIcon } from "@hugeicons/react"
import { ArrowLeft01Icon, Tick02Icon, Copy01Icon, Download01Icon, Loading03Icon, MoreHorizontalIcon, PencilEdit01Icon, Delete01Icon, Link01Icon, Gps01Icon } from "@hugeicons/core-free-icons"
import { cn } from "@/lib/utils"

interface SubtitleTrackInfo {
  lang: string
  type: string
  filename: string
  polished: boolean
}

interface Props {
  archivePath: string
  taskId?: string | null
}

export function ResultPageComplete({ archivePath, taskId }: Props) {
  const pipelineSteps = usePipelineSteps()
  const { archives, refresh: refreshArchives } = useArchives()
  const [archive, setArchive] = useState<ArchiveItem | null>(null)
  const [showDeleteDialog, setShowDeleteDialog] = useState(false)

  // Per-file content state (null = not yet available)
  const [summary, setSummary] = useState<string | null>(null)
  const [transcript, setTranscript] = useState<string | null>(null)
  const [isPolished, setIsPolished] = useState(false)
  const [mindmap, setMindmap] = useState<string | null>(null)
  const [mindmapFit, setMindmapFit] = useState<(() => void) | null>(null)
  const [subtitles, setSubtitles] = useState<Subtitle[]>([])
  const [subtitleTracks, setSubtitleTracks] = useState<SubtitleTrackInfo[]>([])
  const [activeTrackLang, setActiveTrackLang] = useState<string | null>(null)
  const [polishedLang, setPolishedLang] = useState<string | null>(null)
  const [subtitleSourceType, setSubtitleSourceType] = useState<"platform" | "asr" | null>(null)
  const [sourceUrl, setSourceUrl] = useState<string | null>(null)

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

  const [mergeInfo, setMergeInfo] = useState<SpeakerMergeInfo | null>(null)

  const applyRenameLocally = useCallback(
    async (oldName: string, newName: string) => {
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
    },
    [subtitles, archivePath, sep],
  )

  const handleRenameSpeaker = async (oldName: string, newName: string) => {
    if (!taskId) {
      // Legacy archive without taskId — fall back to local-only rename
      await applyRenameLocally(oldName, newName)
      return
    }
    try {
      const res = await api.voiceprints.renameTaskSpeaker(taskId, oldName, newName, "ask")
      if (res.status === "conflict") {
        setMergeInfo({
          oldName,
          newName,
          existingPersonId: res.conflict_person_id ?? "",
          existingPersonName: res.conflict_person_name ?? newName,
          existingSampleCount: res.conflict_sample_count ?? 0,
        })
        return
      }
      // renamed or merged — both mean local SRT should reflect the resolved name
      const appliedName = res.person_name ?? newName
      await applyRenameLocally(oldName, appliedName)
    } catch (err) {
      console.warn("renameTaskSpeaker failed, falling back to local rename:", err)
      await applyRenameLocally(oldName, newName)
    }
  }

  const resolveMerge = async (choice: "merge" | "new" | "cancel") => {
    if (!mergeInfo || !taskId) {
      setMergeInfo(null)
      return
    }
    if (choice === "cancel") {
      setMergeInfo(null)
      return
    }
    try {
      const res = await api.voiceprints.renameTaskSpeaker(
        taskId,
        mergeInfo.oldName,
        mergeInfo.newName,
        choice,
      )
      const appliedName = res.person_name ?? mergeInfo.newName
      await applyRenameLocally(mergeInfo.oldName, appliedName)
    } catch (err) {
      console.warn("Conflict resolution failed:", err)
    }
    setMergeInfo(null)
  }

  // Find archive from list
  useEffect(() => {
    const found = archives.find((a) => a.path === archivePath)
    if (found) {
      setArchive(found)
      const meta = (found.metadata || {}) as Record<string, unknown>
      setSourceUrl((meta.source_url as string | null) ?? null)
      const extra = (meta.extra || {}) as Record<string, unknown>
      const tracks = (extra.subtitle_tracks as SubtitleTrackInfo[] | undefined) ?? []
      setSubtitleTracks(tracks)
      const polished = tracks.find((t) => t.polished)
      setPolishedLang(polished?.lang ?? null)
      if (polished && !activeTrackLang) setActiveTrackLang(polished.lang)
      if (tracks.some((t) => t.type === "asr")) setSubtitleSourceType("asr")
      else if (tracks.length > 0) setSubtitleSourceType("platform")
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
    setMediaUrl(api.filesystem.mediaUrl(arch.media_file))
  }, [])

  useEffect(() => {
    resolveMediaUrl(archive)
  }, [archive, resolveMediaUrl])

  // --- Progressive file loading ---
  const loadFile = useCallback(async (filename: string): Promise<string> => {
    try {
      const data = await api.filesystem.read(archivePath + sep + filename)
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
    // Transcript — prefer polished, fallback to raw
    loadFile("transcript_polished.srt").then((polished) => {
      if (polished) {
        setTranscript(polished)
        setIsPolished(true)
        setSubtitles(parseSRT(polished))
        setSubtitleSourceType((prev) => prev ?? "platform")
        setActiveTrackLang((prev) => prev ?? null)
      } else {
        loadFile("transcript.srt").then((raw) => {
          if (raw) {
            setTranscript(raw)
            setIsPolished(false)
            setSubtitles(parseSRT(raw))
            setSubtitleSourceType((prev) => prev ?? "asr")
          }
        })
      }
    })
  }, [archivePath, loadFile])

  // --- SSE subscription for in-progress tasks ---
  useTaskSSE(taskId, {
    // Snapshot is sent immediately on (re)connect — rebuilds pipeline state
    // when the user navigates back to the result page mid-processing.
    onSnapshot(data) {
      setTaskStatus(data.status)
      setCurrentStep(data.current_step)
      setCompletedSteps(data.completed_steps ?? [])
      if (data.error) setTaskError(data.error)
    },
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

  const selectTrack = useCallback(async (lang: string) => {
    const track = subtitleTracks.find((t) => t.lang === lang)
    if (!track) return
    setActiveTrackLang(lang)
    if (track.polished) {
      const c = await loadFile("transcript_polished.srt")
      if (c) {
        setTranscript(c)
        setIsPolished(true)
        setSubtitles(parseSRT(c))
      }
    } else {
      const c = await loadFile(track.filename)
      if (c) {
        setTranscript(c)
        setIsPolished(false)
        setSubtitles(parseSRT(c))
      }
    }
  }, [subtitleTracks, loadFile])

  const mediaType = archive?.has_video ? "video" : "audio"
  const [displayTitle, setDisplayTitle] = useState<string>("")
  const [editingTitle, setEditingTitle] = useState(false)
  const [titleDraft, setTitleDraft] = useState("")
  const isProcessing = taskStatus === "processing" || taskStatus === "queued"
  const hasContent = summary || transcript || mindmap

  // Sync title from archive
  useEffect(() => {
    const t = archive?.title ?? archivePath.split(/[/\\]/).pop() ?? ""
    setDisplayTitle(t)
  }, [archive, archivePath])

  const startEditTitle = () => {
    setTitleDraft(displayTitle)
    setEditingTitle(true)
  }

  const commitTitle = async () => {
    const trimmed = titleDraft.trim()
    if (!trimmed || trimmed === displayTitle) {
      setEditingTitle(false)
      return
    }
    try {
      await api.archives.rename(archivePath, trimmed)
      setDisplayTitle(trimmed)
      refreshArchives()
    } catch {
      // ignore, revert
    }
    setEditingTitle(false)
  }

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
          <HugeiconsIcon icon={ArrowLeft01Icon} className="h-4 w-4 mr-1" />
          返回
        </Button>
        {editingTitle ? (
          <input
            className="flex-1 text-sm font-medium bg-transparent border-b border-primary outline-none truncate"
            value={titleDraft}
            autoFocus
            onChange={(e) => setTitleDraft(e.target.value)}
            onBlur={commitTitle}
            onKeyDown={(e) => {
              if (e.key === "Enter") commitTitle()
              if (e.key === "Escape") setEditingTitle(false)
            }}
          />
        ) : (
          <div className="flex-1 flex items-center gap-1.5 min-w-0">
            <button
              className="text-sm font-medium truncate text-left hover:text-primary transition-colors group flex items-center gap-1 min-w-0"
              onClick={startEditTitle}
              title="点击编辑标题"
            >
              <span className="truncate">{displayTitle}</span>
              <HugeiconsIcon icon={PencilEdit01Icon} className="h-3 w-3 shrink-0 opacity-0 group-hover:opacity-50 transition-opacity" />
            </button>
            {sourceUrl && /^https?:\/\//i.test(sourceUrl) && (
              <a
                href={sourceUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="shrink-0 p-1 rounded text-muted-foreground hover:text-primary hover:bg-muted transition-colors"
                title="打开原始链接"
              >
                <HugeiconsIcon icon={Link01Icon} className="h-3.5 w-3.5" />
              </a>
            )}
            {subtitleSourceType && (
              <span
                className={cn(
                  "shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium",
                  subtitleSourceType === "platform"
                    ? "bg-sky-500/10 text-sky-700 dark:text-sky-300"
                    : "bg-violet-500/10 text-violet-700 dark:text-violet-300",
                )}
                title={subtitleSourceType === "platform" ? "字幕来自平台" : "字幕由 ASR 生成"}
              >
                {subtitleSourceType === "platform"
                  ? (isPolished ? "Platform+润色" : "Platform")
                  : (isPolished ? "ASR+润色" : "ASR")}
              </span>
            )}
          </div>
        )}
        {isProcessing && (
          <span className="text-xs text-blue-600 flex items-center gap-1">
            <HugeiconsIcon icon={Loading03Icon} className="h-3 w-3 animate-spin" />
            处理中
          </span>
        )}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon-sm">
              <HugeiconsIcon icon={MoreHorizontalIcon} className="h-4 w-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem
              variant="destructive"
              onClick={() => setShowDeleteDialog(true)}
            >
              <HugeiconsIcon icon={Delete01Icon} className="h-4 w-4" />
              删除
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {/* Pipeline progress bar (visible when processing) */}
      {isProcessing && (
        <div className="shrink-0 flex items-center gap-1 px-4 py-1.5 border-b bg-muted/30">
          {pipelineSteps.map((step, i) => {
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
                      <HugeiconsIcon icon={Tick02Icon} className="h-2.5 w-2.5" />
                    ) : isCurrent ? (
                      <HugeiconsIcon icon={Loading03Icon} className="h-2.5 w-2.5 animate-spin text-blue-600" />
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
                {i < pipelineSteps.length - 1 && (
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
                    <HugeiconsIcon icon={Loading03Icon} className="h-6 w-6 animate-spin mx-auto mb-2" />
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
                  {activeTab === "mindmap" && mindmapFit && (
                    <button
                      onClick={mindmapFit}
                      className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                      title="回正视角"
                    >
                      <HugeiconsIcon icon={Gps01Icon} className="h-3.5 w-3.5" />
                    </button>
                  )}
                  {getTabContent()?.content && (
                    <>
                      <button
                        onClick={handleCopy}
                        className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                        title="复制全部内容"
                      >
                        {copied ? <HugeiconsIcon icon={Tick02Icon} className="h-3.5 w-3.5 text-emerald-500" /> : <HugeiconsIcon icon={Copy01Icon} className="h-3.5 w-3.5" />}
                      </button>
                      <button
                        onClick={handleDownload}
                        className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                        title="下载为文件"
                      >
                        <HugeiconsIcon icon={Download01Icon} className="h-3.5 w-3.5" />
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
                        <HugeiconsIcon icon={Loading03Icon} className="h-4 w-4 animate-spin mr-2" />
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
                    {subtitleTracks.length > 1 && (
                      <div className="shrink-0 flex items-center gap-1 px-2 py-1.5 border-b bg-muted/20 overflow-x-auto">
                        <span className="text-[10px] text-muted-foreground shrink-0">语言：</span>
                        {subtitleTracks.map((t) => {
                          const active = (activeTrackLang ?? polishedLang) === t.lang
                          return (
                            <button
                              key={t.lang}
                              onClick={() => selectTrack(t.lang)}
                              className={cn(
                                "shrink-0 rounded px-2 py-0.5 text-[11px] transition-colors",
                                active
                                  ? "bg-primary text-primary-foreground"
                                  : "bg-muted hover:bg-muted/70 text-foreground",
                              )}
                              title={t.polished ? "已润色" : "原始字幕"}
                            >
                              {t.lang}
                              {t.polished && <span className="ml-1 text-[9px] opacity-80">✓</span>}
                            </button>
                          )
                        })}
                      </div>
                    )}
                    {subtitles.length > 0 ? (
                      <TranscriptTab
                        subtitles={subtitles}
                        currentSegmentIndex={currentSegmentIndex}
                        autoScroll={autoScroll}
                        onSegmentClick={(sub) => seekTo(sub.startTime)}
                        onManualScroll={onManualScroll}
                        srtPath={archivePath + sep + (
                          activeTrackLang && !subtitleTracks.find((t) => t.lang === activeTrackLang)?.polished
                            ? `transcript.${activeTrackLang}.srt`
                            : (isPolished ? "transcript_polished.srt" : "transcript.srt")
                        )}
                        onSubtitlesChange={setSubtitles}
                      />
                    ) : isProcessing ? (
                      <div className="flex items-center justify-center h-full text-muted-foreground">
                        <HugeiconsIcon icon={Loading03Icon} className="h-4 w-4 animate-spin mr-2" />
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
                      <MindmapViewer markdown={mindmap} fillContainer title={displayTitle} onFitReady={(fn) => setMindmapFit(() => fn)} />
                    ) : isProcessing ? (
                      <div className="flex items-center justify-center h-full text-muted-foreground">
                        <HugeiconsIcon icon={Loading03Icon} className="h-4 w-4 animate-spin mr-2" />
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

      {/* Speaker merge confirmation */}
      <SpeakerMergeDialog info={mergeInfo} onResolve={resolveMerge} />
    </div>
  )
}
