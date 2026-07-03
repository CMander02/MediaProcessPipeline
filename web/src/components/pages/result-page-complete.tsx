/**
 * Unified result viewer — handles both in-progress and completed archives.
 * Loads files progressively and subscribes to SSE for real-time updates.
 */
import { useCallback, useEffect, useRef, useState } from "react"
import type { ReactNode } from "react"
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
import { Progress } from "@/components/ui/progress"
import { DeleteConfirmDialog } from "@/components/delete-confirm-dialog"
import { PlatformIcon } from "@/components/platform-icon"
import { SpeakerMergeDialog, type SpeakerMergeInfo } from "@/components/speaker-merge-dialog"
import { useArchives, type ArchiveItem } from "@/hooks/use-archives"
import { useMediaSync } from "@/hooks/use-media-sync"
import { useViewPosition } from "@/hooks/use-view-position"
import { useTaskSSE, type FileReadyEvent, type StepEvent } from "@/hooks/use-task-sse"
import { parseSRT, subtitlesToSRT, type Subtitle } from "@/lib/srt"
import { navigate } from "@/lib/router"
import { api, type Task, type TaskFlowSnapshot, type TaskTimelineEvent } from "@/lib/api"
import { openExternalUrl } from "@/lib/tauri"
import { MediaPlayer } from "@/components/result/media-player"
import { SpeakerPanel } from "@/components/result/speaker-panel"
import { TranscriptTab, type MindmapTocNode } from "@/components/result/transcript-tab"
import { SummaryTab } from "@/components/result/summary-tab"
import { MindmapViewer } from "@/components/result/mindmap-viewer"
import { ImageNoteViewer, type ImageDescription } from "@/components/result/image-note-viewer"
import { MarkdownRenderer } from "@/components/result/markdown-renderer"
import { HugeiconsIcon } from "@hugeicons/react"
import {
  ArrowLeft01Icon,
  Tick02Icon,
  Copy01Icon,
  Download01Icon,
  Loading03Icon,
  MoreHorizontalIcon,
  PencilEdit01Icon,
  Delete01Icon,
  Link01Icon,
  Gps01Icon,
  Video01Icon,
  MusicNote01Icon,
  Image01Icon,
  Note01Icon,
  ListTreeIcon,
} from "@hugeicons/core-free-icons"
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

function normalizeArchivePath(path: string): string {
  return path.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase()
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null
}

function firstHttpUrl(...values: unknown[]): string | null {
  for (const value of values) {
    if (typeof value !== "string") continue
    const trimmed = value.trim()
    if (/^https?:\/\//i.test(trimmed)) return trimmed
  }
  return null
}

function timelineEventKey(event: TaskTimelineEvent): string {
  return `${event.id}:${event.event_type}:${event.timestamp}`
}

function timelineTime(timestamp: string): string {
  return timestamp.split("T")[1]?.slice(0, 8) ?? ""
}

function timelineMessage(event: TaskTimelineEvent): string {
  if (event.message) return event.message
  if (typeof event.data.error === "string") return event.data.error
  if (typeof event.data.reason === "string") return event.data.reason
  return event.event_type
}

function timelineStatusText(event: TaskTimelineEvent, stepLabels: Record<string, string> = {}): string {
  if (event.event_type === "queued") return "任务已进入队列"
  if (event.event_type === "processing") return "开始处理任务"
  if (event.event_type === "completed") return "处理完成"
  if (event.event_type === "failed") return timelineMessage(event)
  const stepLabel = (event.step_id && stepLabels[event.step_id]) || (event.stage && stepLabels[event.stage])
  const message = timelineMessage(event)
  if (message && message !== event.stage && message !== event.step_id) return message
  return stepLabel ?? message
}

function timelineStatusClass(level: string): string {
  if (level === "error") return "border-destructive/40 bg-destructive/5 text-destructive"
  if (level === "warning") return "border-amber-300/60 bg-amber-50 text-amber-700 dark:border-amber-500/40 dark:bg-amber-950/30 dark:text-amber-300"
  return "border-border bg-muted/40 text-muted-foreground"
}

function resolveSourceUrl(metadata: Record<string, unknown>): string | null {
  const extra = asRecord(metadata.extra)
  const nested = asRecord(extra?.metadata) ?? asRecord(extra?.raw) ?? asRecord(extra?.info)
  return firstHttpUrl(
    metadata.source_url,
    metadata.original_url,
    metadata.webpage_url,
    metadata.url,
    extra?.source_url,
    extra?.original_url,
    extra?.webpage_url,
    extra?.url,
    nested?.source_url,
    nested?.original_url,
    nested?.webpage_url,
    nested?.url,
  )
}

function resolveNoteMediaSrc(src: string | undefined, archivePath: string, sep: string): string | undefined {
  if (!src) return src
  if (/^(?:https?:|data:|blob:)/i.test(src)) return src
  const normalized = src.replace(/\\/g, "/")
  if (/^[A-Za-z]:\//.test(normalized) || normalized.startsWith("/")) {
    return api.filesystem.mediaUrl(src)
  }
  return api.filesystem.mediaUrl(archivePath + sep + normalized.replace(/\//g, sep))
}

function NoteMarkdown({ content, archivePath, sep }: { content: string; archivePath: string; sep: string }) {
  return (
    <div className="prose prose-sm max-w-none dark:prose-invert">
      <MarkdownRenderer
        components={{
          img: ({ src, alt }) => (
            <img
              src={resolveNoteMediaSrc(src, archivePath, sep)}
              alt={alt ?? ""}
              className="mx-auto my-4 max-h-[520px] w-full rounded-md object-contain"
              loading="lazy"
            />
          ),
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noreferrer">{children}</a>
          ),
        }}
      >
        {content}
      </MarkdownRenderer>
    </div>
  )
}

type ArticleMarkdownSegment =
  | { kind: "markdown"; content: string }
  | { kind: "figure"; alt: string; src: string; caption: string | null }

function markdownHasInlineImages(content: string | null): boolean {
  if (!content) return false
  return /!\[[^\]]*]\([^)]+\)|<img\s/i.test(content)
}

function unescapeMarkdownText(value: string): string {
  return value.replace(/\\([\\[\]])/g, "$1")
}

function normalizeCaptionText(value: string): string {
  return unescapeMarkdownText(value).replace(/\s+/g, " ").trim()
}

function parseArticleMarkdownSegments(content: string): ArticleMarkdownSegment[] {
  const lines = content.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n")
  const segments: ArticleMarkdownSegment[] = []
  const pending: string[] = []

  const flushPending = () => {
    const markdown = pending.join("\n").trim()
    if (markdown) segments.push({ kind: "markdown", content: markdown })
    pending.length = 0
  }

  for (let i = 0; i < lines.length;) {
    const match = lines[i].match(/^!\[([^\]]*(?:\\][^\]]*)*)]\(([^)]+)\)\s*$/)
    if (!match) {
      pending.push(lines[i])
      i += 1
      continue
    }

    const alt = unescapeMarkdownText(match[1]).trim()
    const src = match[2].trim()
    let cursor = i + 1
    while (cursor < lines.length && lines[cursor].trim() === "") cursor += 1

    const captionStart = cursor
    const captionLines: string[] = []
    while (cursor < lines.length && lines[cursor].trim() !== "") {
      captionLines.push(lines[cursor])
      cursor += 1
    }
    const caption = captionLines.join("\n").trim()
    const shouldConsumeCaption =
      Boolean(caption) &&
      alt !== "图片" &&
      normalizeCaptionText(caption) === normalizeCaptionText(alt)

    flushPending()
    segments.push({
      kind: "figure",
      alt,
      src,
      caption: shouldConsumeCaption ? caption : null,
    })
    i = shouldConsumeCaption ? cursor : i + 1

    if (!shouldConsumeCaption && captionStart > i) {
      while (i < captionStart && lines[i]?.trim() === "") {
        pending.push(lines[i])
        i += 1
      }
    }
  }

  flushPending()
  return segments
}

function ArticleNoteMarkdown({ content, archivePath, sep }: { content: string; archivePath: string; sep: string }) {
  const segments = parseArticleMarkdownSegments(content)
  const markdownComponents = {
    img: ({ src, alt }: { src?: string; alt?: string }) => (
      <img
        src={resolveNoteMediaSrc(src, archivePath, sep)}
        alt={alt ?? ""}
        className="mx-auto my-4 max-h-[520px] w-full rounded-md object-contain"
        loading="lazy"
      />
    ),
    a: ({ href, children }: { href?: string; children?: ReactNode }) => (
      <a href={href} target="_blank" rel="noreferrer">{children}</a>
    ),
  }

  return (
    <div className="prose prose-sm max-w-none dark:prose-invert">
      {segments.map((segment, index) => {
        if (segment.kind === "markdown") {
          return (
            <MarkdownRenderer key={`markdown-${index}`} components={markdownComponents}>
              {segment.content}
            </MarkdownRenderer>
          )
        }
        return (
          <figure key={`figure-${index}`} className="my-5">
            <img
              src={resolveNoteMediaSrc(segment.src, archivePath, sep)}
              alt={segment.alt}
              className="mx-auto max-h-[640px] w-full rounded-md object-contain"
              loading="lazy"
            />
            {segment.caption ? (
              <figcaption className="mx-auto mt-2 max-w-2xl text-center text-xs leading-6 text-muted-foreground [&_a]:underline [&_code]:rounded [&_code]:bg-muted [&_code]:px-1 [&_p]:m-0">
                <MarkdownRenderer
                  components={{
                    a: ({ href, children }) => (
                      <a href={href} target="_blank" rel="noreferrer">{children}</a>
                    ),
                  }}
                >
                  {segment.caption}
                </MarkdownRenderer>
              </figcaption>
            ) : null}
          </figure>
        )
      })}
    </div>
  )
}

function ArticleNoteReader({
  content,
  archivePath,
  sep,
  descriptions,
  isProcessing,
}: {
  content: string | null
  archivePath: string
  sep: string
  descriptions: ImageDescription[]
  isProcessing?: boolean
}) {
  const showLocalImages = !markdownHasInlineImages(content)
  const localImages = showLocalImages ? descriptions.filter((item) => item.image_path) : []

  return (
    <div className="h-full overflow-y-auto rounded-lg border bg-background">
      <div className="mx-auto max-w-3xl px-6 py-6">
        {content ? (
          <ArticleNoteMarkdown content={content} archivePath={archivePath} sep={sep} />
        ) : isProcessing ? (
          <div className="flex h-40 items-center justify-center text-muted-foreground">
            <HugeiconsIcon icon={Loading03Icon} className="mr-2 h-4 w-4 animate-spin" />
            <span className="text-sm">等待正文...</span>
          </div>
        ) : (
          <div className="flex h-40 items-center justify-center text-sm text-muted-foreground">
            暂无正文
          </div>
        )}
        {localImages.length > 0 && (
          <div className="mt-6 space-y-6">
            {localImages.map((item) => (
              <figure key={item.index} className="space-y-2">
                <img
                  src={api.filesystem.mediaUrl(item.image_path)}
                  alt={`图片 ${item.index + 1}`}
                  className="mx-auto max-h-[640px] w-full rounded-md object-contain"
                  loading="lazy"
                />
                {item.text ? (
                  <figcaption className="whitespace-pre-wrap text-xs leading-6 text-muted-foreground">
                    {item.text}
                  </figcaption>
                ) : null}
              </figure>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export function ResultPageComplete({ archivePath, taskId: taskIdProp }: Props) {
  const { archives, refresh: refreshArchives } = useArchives()
  const [archive, setArchive] = useState<ArchiveItem | null>(null)
  const [showDeleteDialog, setShowDeleteDialog] = useState(false)
  // Resolve taskId: prefer prop (from URL), fall back to archive list lookup.
  // undefined = not yet resolved; null = confirmed no taskId; string = resolved
  const [resolvedTaskId, setResolvedTaskId] = useState<string | null | undefined>(
    taskIdProp !== undefined ? taskIdProp : undefined
  )

  // Per-file content state (null = not yet available)
  const [summary, setSummary] = useState<string | null>(null)
  const [transcript, setTranscript] = useState<string | null>(null)
  const [isPolished, setIsPolished] = useState(false)
  const [mindmap, setMindmap] = useState<string | null>(null)
  const [mindmapTree, setMindmapTree] = useState<MindmapTocNode | null>(null)
  const [detail, setDetail] = useState<string | null>(null)
  const [mindmapFit, setMindmapFit] = useState<(() => void) | null>(null)
  const [subtitles, setSubtitles] = useState<Subtitle[]>([])
  const [subtitleTracks, setSubtitleTracks] = useState<SubtitleTrackInfo[]>([])
  const [activeTrackLang, setActiveTrackLang] = useState<string | null>(null)
  const [polishedLang, setPolishedLang] = useState<string | null>(null)
  const [subtitleSourceType, setSubtitleSourceType] = useState<"platform" | "asr" | null>(null)
  const [sourceUrl, setSourceUrl] = useState<string | null>(null)
  const [platform, setPlatform] = useState<string | null>(null)
  const [uploader, setUploader] = useState<string | null>(null)
  const [contentSubtype, setContentSubtype] = useState<string | null>(null)
  const [noteText, setNoteText] = useState<string | null>(null)
  const [imageDescriptions, setImageDescriptions] = useState<ImageDescription[]>([])
  const [activeImageIdx, setActiveImageIdx] = useState(0)

  // Pipeline progress state
  const [taskStatus, setTaskStatus] = useState<string | null>(null)
  const [completedSteps, setCompletedSteps] = useState<string[]>([])
  const [currentStep, setCurrentStep] = useState<string | null>(null)
  const [taskError, setTaskError] = useState<string | null>(null)
  const [taskFlow, setTaskFlow] = useState<TaskFlowSnapshot | null>(null)
  const [timelineEvents, setTimelineEvents] = useState<TaskTimelineEvent[]>([])

  // Media URL state — may change when source/ is deleted after completion
  const [mediaUrl, setMediaUrl] = useState<string | null>(null)

  useEffect(() => {
    setArchive(null)
    setResolvedTaskId(taskIdProp !== undefined ? taskIdProp : undefined)
    setSummary(null)
    setTranscript(null)
    setIsPolished(false)
    setMindmap(null)
    setMindmapTree(null)
    setDetail(null)
    setSubtitles([])
    setSubtitleTracks([])
    setActiveTrackLang(null)
    setPolishedLang(null)
    setSubtitleSourceType(null)
    setSourceUrl(null)
    setPlatform(null)
    setUploader(null)
    setContentSubtype(null)
    setNoteText(null)
    setImageDescriptions([])
    setActiveImageIdx(0)
    setTaskStatus(null)
    setCompletedSteps([])
    setCurrentStep(null)
    setTaskError(null)
    setTaskFlow(null)
    setTimelineEvents([])
    setMediaUrl(null)
  }, [archivePath, taskIdProp])

  // Persist and restore viewing position
  const { updateMediaTime, updateActiveTab, getSavedPosition } = useViewPosition(archivePath)
  const savedPos = useRef(getSavedPosition())
  const [activeTab, setActiveTab] = useState(savedPos.current.activeTab || "summary")

  useEffect(() => {
    const restored = getSavedPosition()
    setActiveTab(restored.activeTab || "summary")
  }, [archivePath, taskIdProp, getSavedPosition])

  const { bindMedia, currentTime, duration, currentSegmentIndex, autoScroll, seekTo, onManualScroll } =
    useMediaSync({
      subtitles,
      initialTime: savedPos.current.mediaTime,
      onTimeUpdate: updateMediaTime,
    })

  const mergeTimelineEvent = useCallback((event: TaskTimelineEvent) => {
    setTimelineEvents((prev) => {
      const key = timelineEventKey(event)
      if (prev.some((item) => timelineEventKey(item) === key)) return prev
      return [...prev, event].slice(-200)
    })
  }, [])

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
    if (!resolvedTaskId) {
      // Legacy archive without taskId — fall back to local-only rename
      await applyRenameLocally(oldName, newName)
      return
    }
    try {
      const res = await api.voiceprints.renameTaskSpeaker(resolvedTaskId, oldName, newName, "ask")
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
    if (!mergeInfo || !resolvedTaskId) {
      setMergeInfo(null)
      return
    }
    if (choice === "cancel") {
      setMergeInfo(null)
      return
    }
    try {
      const res = await api.voiceprints.renameTaskSpeaker(
        resolvedTaskId,
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

  const applyMetadataState = useCallback((metadata: Record<string, unknown>) => {
    setSourceUrl(resolveSourceUrl(metadata))
    setPlatform((metadata.platform as string | null) ?? null)
    setUploader((metadata.uploader as string | null) ?? null)
    setContentSubtype((metadata.content_subtype as string | null) ?? null)
    setNoteText((metadata.description as string | null) ?? null)

    const extra = asRecord(metadata.extra)
    const tracks = (extra?.subtitle_tracks as SubtitleTrackInfo[] | undefined) ?? []
    setSubtitleTracks(tracks)
    const polished = tracks.find((t) => t.polished)
    setPolishedLang(polished?.lang ?? null)
    if (polished) setActiveTrackLang((current) => current ?? polished.lang)
    if (tracks.some((t) => t.type === "asr")) setSubtitleSourceType("asr")
    else if (tracks.length > 0) setSubtitleSourceType("platform")
  }, [])

  const applyTaskSnapshot = useCallback((task: Task) => {
    setTaskStatus(task.status)
    setCurrentStep(task.current_step)
    setCompletedSteps(task.completed_steps ?? [])
    setTaskError(task.error)
    setTaskFlow(task.flow ?? null)

    const metadata = asRecord(asRecord(task.result)?.metadata)
    if (metadata) {
      applyMetadataState(metadata)
    } else if (task.content_subtype) {
      setContentSubtype(task.content_subtype)
    } else if (task.flow?.content_subtype) {
      setContentSubtype(task.flow.content_subtype)
    }

    const descs = asRecord(task.result)?.image_descriptions as ImageDescription[] | undefined
    if (descs && descs.length > 0) setImageDescriptions(descs)
  }, [applyMetadataState])

  // Find archive from list
  useEffect(() => {
    const found = archives.find((a) => a.path === archivePath)
    if (found) {
      setArchive(found)
      const meta = (found.metadata || {}) as Record<string, unknown>
      applyMetadataState(meta)
      // Resolve taskId from archive list if not already known from URL
      if (resolvedTaskId === undefined) {
        setResolvedTaskId(found.task_id ?? null)
      }
      // Determine initial task status from archive
      if (found.processing) {
        setTaskStatus("processing")
      } else if (!taskIdProp) {
        setTaskStatus("completed")
      }
    }
  }, [archives, archivePath, taskIdProp, resolvedTaskId, applyMetadataState])

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
  const readFilePath = useCallback(async (path: string): Promise<string> => {
    try {
      const data = await api.filesystem.read(path)
      return data.content ?? ""
    } catch {
      return ""
    }
  }, [])

  const loadFile = useCallback(async (filename: string, basePath = archivePath): Promise<string> => {
    const baseSep = basePath.includes("\\") ? "\\" : "/"
    return readFilePath(basePath + baseSep + filename)
  }, [archivePath, readFilePath])

  const applyTranscriptContent = useCallback((content: string, polished: boolean) => {
    setTranscript(content)
    setIsPolished(polished)
    setSubtitles(parseSRT(content))
    setSubtitleSourceType((prev) => prev ?? (polished ? "platform" : "asr"))
  }, [])

  const loadGeneratedContent = useCallback(async (basePath = archivePath) => {
    const [
      summaryMd,
      mindmapMd,
      mindmapJson,
      detailMd,
      sourceMd,
      polishedSrt,
      rawSrt,
    ] = await Promise.all([
      loadFile("summary.md", basePath),
      loadFile("mindmap.md", basePath),
      loadFile("mindmap.json", basePath),
      loadFile("detail.md", basePath),
      loadFile("source.md", basePath),
      loadFile("transcript_polished.srt", basePath),
      loadFile("transcript.srt", basePath),
    ])

    if (summaryMd) setSummary(summaryMd)
    if (mindmapMd) setMindmap(mindmapMd)
    if (mindmapJson) {
      try {
        setMindmapTree(JSON.parse(mindmapJson) as MindmapTocNode)
      } catch (err) {
        console.warn("Failed to parse mindmap.json:", err)
      }
    }
    if (detailMd) setDetail(detailMd)
    if (sourceMd) setNoteText(sourceMd)

    if (polishedSrt) {
      applyTranscriptContent(polishedSrt, true)
      setActiveTrackLang((prev) => prev ?? null)
    } else if (rawSrt) {
      applyTranscriptContent(rawSrt, false)
    }
  }, [archivePath, applyTranscriptContent, loadFile])

  const refreshTaskSnapshot = useCallback(async () => {
    if (!resolvedTaskId) return null
    try {
      const task = await api.tasks.get(resolvedTaskId)
      applyTaskSnapshot(task)
      return task
    } catch {
      return null
    }
  }, [resolvedTaskId, applyTaskSnapshot])

  useEffect(() => {
    if (!resolvedTaskId) return
    let cancelled = false
    Promise.all([
      api.tasks.get(resolvedTaskId),
      api.tasks.timeline(resolvedTaskId),
    ])
      .then(([task, timeline]) => {
        if (cancelled) return
        applyTaskSnapshot(task)
        setTimelineEvents(timeline.events ?? [])
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [resolvedTaskId, applyTaskSnapshot])

  // Load image descriptions for image_note content type
  const loadImageDescriptions = useCallback(async () => {
    if (!archivePath) return
    // Preferred: read image_descriptions directly from task result (set by pipeline)
    if (resolvedTaskId) {
      try {
        const task = await api.tasks.get(resolvedTaskId)
        const descs = task.result?.image_descriptions as ImageDescription[] | undefined
        if (descs && descs.length > 0) {
          setImageDescriptions(descs)
          return
        }
      } catch {}
    }
    // Fall back: probe numbered image files on disk (count limited by images/ directory)
    const descs: ImageDescription[] = []
    try {
      const imagesDir = archivePath + sep + "images"
      const listing = await api.filesystem.browse(imagesDir, "file")
      const images = (listing.items ?? [])
        .filter((item) => !item.is_dir && /\.(?:jpe?g|png|webp|gif|bmp)$/i.test(item.name))
        .sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }))
      for (const item of images) {
        const stem = item.name.replace(/\.[^.]+$/, "")
        const descPath = archivePath + sep + "descriptions" + sep + `${stem}.md`
        let text = ""
        try { text = (await api.filesystem.read(descPath)).content ?? "" } catch {}
        const index = Number.parseInt(stem, 10)
        descs.push({
          index: Number.isFinite(index) ? index : descs.length,
          image_path: item.path,
          kind: "content",
          text,
        })
      }
    } catch {}
    for (let i = descs.length > 0 ? 30 : 0; i < 30; i++) {
      const imgPath = archivePath + sep + "images" + sep + `${String(i).padStart(2, "0")}.jpg`
      const descPath = archivePath + sep + "descriptions" + sep + `${String(i).padStart(2, "0")}.md`
      try {
        const check = await api.filesystem.read(imgPath)
        if (!check || !check.success) break
        let text = ""
        try { text = (await api.filesystem.read(descPath)).content ?? "" } catch {}
        descs.push({ index: i, image_path: imgPath, kind: "content", text })
      } catch { break }
    }
    if (descs.length > 0) setImageDescriptions(descs)
  }, [archivePath, sep, resolvedTaskId])

  useEffect(() => {
    // Wait until resolvedTaskId is known (undefined = not yet resolved, null/string = resolved)
    if (contentSubtype === "image_note" && resolvedTaskId !== undefined) loadImageDescriptions()
  }, [contentSubtype, loadImageDescriptions, resolvedTaskId])

  // Load files independently on mount
  useEffect(() => {
    loadGeneratedContent()
  }, [archivePath, loadGeneratedContent])

  // --- SSE subscription for in-progress tasks ---
  useTaskSSE(resolvedTaskId, {
    // Snapshot is sent immediately on (re)connect — rebuilds pipeline state
    // when the user navigates back to the result page mid-processing.
    onSnapshot(data) {
      setTaskStatus(data.status)
      setCurrentStep(data.current_step)
      setCompletedSteps(data.completed_steps ?? [])
      if (data.flow) setTaskFlow(data.flow)
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
    onFlow(data) {
      if (data.flow) setTaskFlow(data.flow)
    },
    onTimeline(event) {
      mergeTimelineEvent(event)
    },
    onFileReady(data: FileReadyEvent) {
      const { file, path } = data
      const loadReadyFile = () => readFilePath(path || archivePath + sep + file)
      if (file === "transcript_polished.srt") {
        loadReadyFile().then((c) => {
          if (c) {
            applyTranscriptContent(c, true)
          }
        })
      } else if (file === "transcript.srt" && !isPolished) {
        loadReadyFile().then((c) => {
          if (c) {
            applyTranscriptContent(c, false)
          }
        })
      } else if (file === "summary.md") {
        loadReadyFile().then((c) => { if (c) setSummary(c) })
      } else if (file === "mindmap.md") {
        loadReadyFile().then((c) => { if (c) setMindmap(c) })
      } else if (file === "mindmap.json") {
        loadReadyFile().then((c) => {
          if (!c) return
          try {
            setMindmapTree(JSON.parse(c) as MindmapTocNode)
          } catch (err) {
            console.warn("Failed to parse mindmap.json:", err)
          }
        })
      } else if (file === "detail.md") {
        loadReadyFile().then((c) => { if (c) setDetail(c) })
      } else if (file === "source.md") {
        loadReadyFile().then((c) => { if (c) setNoteText(c) })
      } else if (file === "metadata.json") {
        refreshArchives(true)
      }
    },
    async onCompleted(data) {
      setTaskStatus("completed")
      const task = await refreshTaskSnapshot()
      const outputDir =
        data.output_dir ??
        (task?.result?.output_dir as string | undefined) ??
        ((task?.result?.archive as Record<string, unknown> | undefined)?.output_dir as string | undefined)

      await Promise.all([
        refreshArchives(true),
        loadGeneratedContent(outputDir || archivePath),
      ])

      if (outputDir && normalizeArchivePath(outputDir) !== normalizeArchivePath(archivePath)) {
        const tid = resolvedTaskId ? `&taskId=${encodeURIComponent(resolvedTaskId)}` : ""
        navigate(`#/result/archive?path=${encodeURIComponent(outputDir)}${tid}`, { replace: true })
      }
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
  const sourceHref = firstHttpUrl(sourceUrl)
  const isImageNote = contentSubtype === "image_note"
  const isTextNote = contentSubtype === "text_note"
  const archiveMetadata = (archive?.metadata || {}) as Record<string, unknown>
  const archiveExtra = asRecord(archiveMetadata.extra)
  const bilibiliType = typeof archiveExtra?.bilibili_type === "string" ? archiveExtra.bilibili_type : null
  const isArticleNote = platform === "bilibili_opus" && bilibiliType === "article"
  const isPureWebpage = platform === "webpage" && isTextNote
  const isNoteContent = isImageNote || isTextNote
  const headerMediaIcon = isImageNote || archive?.has_image
    ? Image01Icon
    : isTextNote
      ? Note01Icon
    : mediaType === "video"
      ? Video01Icon
      : MusicNote01Icon
  const headerMediaLabel = isImageNote || archive?.has_image
    ? isArticleNote ? "专栏" : "图文"
    : isTextNote
      ? "正文"
    : mediaType === "video"
      ? "视频"
      : "音频"
  const [displayTitle, setDisplayTitle] = useState<string>("")
  const [editingTitle, setEditingTitle] = useState(false)
  const [titleDraft, setTitleDraft] = useState("")
  const isProcessing = taskStatus === "processing" || taskStatus === "queued"
  const showFlowDiagnostics = isProcessing || taskStatus === "failed"
  const flowCompletedSteps = taskFlow?.completed_steps ?? []
  const recentTimelineEvents = timelineEvents
    .filter((event) => event.event_type !== "file_ready")
    .slice(-8)
  const flowStepLabels = Object.fromEntries((taskFlow?.steps ?? []).map((step) => [step.id, step.label]))
  const seenStatusLabels = new Set<string>()
  const latestStatusEvents = recentTimelineEvents
    .slice()
    .reverse()
    .filter((event) => {
      const label = timelineStatusText(event, flowStepLabels)
      if (seenStatusLabels.has(label)) return false
      seenStatusLabels.add(label)
      return true
    })
    .slice(0, 3)
    .reverse()
  const latestStatusEvent = latestStatusEvents[latestStatusEvents.length - 1]
  const flowProgress = Math.round((taskFlow?.progress ?? 0) * 100)
  const flowStatusLabel = taskFlow?.current_step_label ?? taskFlow?.current_step ?? timelineStatusText(latestStatusEvent ?? {
    id: 0,
    task_id: "",
    event_type: isProcessing ? "processing" : "queued",
    level: "info",
    data: {},
    timestamp: "",
  }, flowStepLabels)

  useEffect(() => {
    if (isPureWebpage && activeTab === "transcript") {
      setActiveTab("summary")
      updateActiveTab("summary")
    } else if ((!isImageNote || isArticleNote) && activeTab === "source") {
      setActiveTab("summary")
      updateActiveTab("summary")
    }
  }, [activeTab, isArticleNote, isImageNote, isPureWebpage, updateActiveTab])

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
      refreshArchives(true)
    } catch {
      // ignore, revert
    }
    setEditingTitle(false)
  }

  const [copied, setCopied] = useState(false)

  const getTabContent = () => {
    if (activeTab === "summary") return { content: summary, suffix: "摘要", ext: "md" }
    if (activeTab === "source" && isImageNote) return { content: noteText, suffix: "原帖", ext: "md" }
    if (activeTab === "transcript" && isTextNote && !isPureWebpage) return { content: noteText, suffix: "正文", ext: "md" }
    if (activeTab === "transcript") return { content: transcript, suffix: "字幕", ext: "srt" }
    if (activeTab === "mindmap") return { content: mindmap, suffix: "导图", ext: "md" }
    if (activeTab === "detail") return { content: detail, suffix: "视频详情", ext: "md" }
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

  const handleOpenSource = useCallback(() => {
    if (!sourceHref) return
    void openExternalUrl(sourceHref)
  }, [sourceHref])

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
            <div className="flex shrink-0 items-center gap-1.5 text-muted-foreground">
              {platform ? (
                sourceHref ? (
                  <button
                    type="button"
                    onClick={handleOpenSource}
                    className="rounded p-1 transition-colors hover:bg-muted hover:text-primary"
                    title={uploader ? `打开 ${uploader}` : "打开原始来源"}
                  >
                    <PlatformIcon platform={platform} uploader={uploader} className="h-4 w-4" />
                  </button>
                ) : (
                  <span className="p-1" title={uploader ?? platform}>
                    <PlatformIcon platform={platform} uploader={uploader} className="h-4 w-4" />
                  </span>
                )
              ) : sourceHref ? (
                <button
                  type="button"
                  onClick={handleOpenSource}
                  className="rounded p-1 transition-colors hover:bg-muted hover:text-primary"
                  title="打开原始链接"
                >
                  <HugeiconsIcon icon={Link01Icon} className="h-3.5 w-3.5" />
                </button>
              ) : null}
              <span className="rounded p-1" title={headerMediaLabel}>
                <HugeiconsIcon icon={headerMediaIcon} className="h-3.5 w-3.5" strokeWidth={1.75} />
              </span>
              {summary && (
                <span className="rounded p-1" title="摘要">
                  <HugeiconsIcon icon={Note01Icon} className="h-3.5 w-3.5" strokeWidth={1.75} />
                </span>
              )}
              {mindmap && (
                <span className="rounded p-1" title="导图">
                  <HugeiconsIcon icon={ListTreeIcon} className="h-3.5 w-3.5" strokeWidth={1.75} />
                </span>
              )}
              {subtitleSourceType && (
                <span
                  className={cn(
                    "rounded px-1.5 py-0.5 text-[10px] font-medium",
                    subtitleSourceType === "platform"
                      ? "text-sky-700 dark:text-sky-300"
                      : "text-violet-700 dark:text-violet-300",
                  )}
                  title={subtitleSourceType === "platform" ? "字幕来自平台" : "字幕由 ASR 生成"}
                >
                  {subtitleSourceType === "platform" ? "平台" : "ASR"}
                </span>
              )}
              {isPolished && (
                <span className="rounded p-1 text-primary" title="已润色">
                  <HugeiconsIcon icon={PencilEdit01Icon} className="h-3.5 w-3.5" strokeWidth={1.75} />
                </span>
              )}
            </div>
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

      {showFlowDiagnostics && (taskFlow || recentTimelineEvents.length > 0) && (
        <div className="shrink-0 border-b bg-background px-4 py-3">
          {taskFlow && (
            <>
              <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
                <div className="flex min-w-0 flex-wrap items-center gap-2 text-sm">
                  <span className="font-medium text-foreground">{taskFlow.label}</span>
                  <span className="rounded bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
                    {taskFlow.platform}
                  </span>
                  <span className="text-blue-600 dark:text-blue-400">{flowStatusLabel}</span>
                </div>
                <span className="shrink-0 text-sm tabular-nums text-muted-foreground">{flowProgress}%</span>
              </div>
              <Progress value={flowProgress} className="mt-2 h-1.5" />
            </>
          )}
          {taskFlow?.steps?.length ? (
            <div className="mt-3 flex flex-wrap items-center gap-1.5">
              {taskFlow.steps.map((step) => {
                const isDone = flowCompletedSteps.includes(step.id)
                const isCurrent = taskFlow.current_step === step.id
                return (
                  <span
                    key={step.id}
                    className={cn(
                      "inline-flex h-7 items-center gap-1.5 rounded-md border px-2 text-xs transition-colors",
                      isDone && "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-300",
                      isCurrent && !isDone && "border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-900 dark:bg-blue-950 dark:text-blue-300",
                      !isDone && !isCurrent && "border-border bg-muted/30 text-muted-foreground",
                    )}
                  >
                    {isDone ? (
                      <HugeiconsIcon icon={Tick02Icon} className="h-3 w-3" />
                    ) : isCurrent ? (
                      <HugeiconsIcon icon={Loading03Icon} className="h-3 w-3 animate-spin" />
                    ) : (
                      <span className="h-1.5 w-1.5 rounded-full bg-current opacity-35" />
                    )}
                    {step.label}
                  </span>
                )
              })}
            </div>
          ) : null}
          {latestStatusEvents.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {latestStatusEvents.map((event) => (
                <span
                  key={timelineEventKey(event)}
                  className={cn(
                    "inline-flex min-w-0 max-w-full items-center gap-1.5 rounded-md border px-2 py-1 text-xs",
                    timelineStatusClass(event.level),
                  )}
                  title={timelineStatusText(event, flowStepLabels)}
                >
                  <span className="shrink-0 tabular-nums opacity-70">{timelineTime(event.timestamp)}</span>
                  <span className="min-w-0 truncate">{timelineStatusText(event, flowStepLabels)}</span>
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Error display */}
      {taskStatus === "failed" && taskError && (
        <div className="shrink-0 mx-4 mt-2 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
          {taskError}
        </div>
      )}

      {/* Main content area — three-column layout */}
      <div className="flex-1 min-h-0 relative">
        <ResizablePanelGroup
          orientation="horizontal"
          className="absolute inset-0"
        >
          {/* Center panel — media preview */}
          <ResizablePanel defaultSize="50%" minSize="20%" maxSize="70%">
            <div className="h-full overflow-y-auto p-4 space-y-3">
              {isArticleNote ? (
                <ArticleNoteReader
                  content={noteText}
                  archivePath={archivePath}
                  sep={sep}
                  descriptions={imageDescriptions}
                  isProcessing={isProcessing}
                />
              ) : isImageNote ? (
                <div className="h-full">
                  <ImageNoteViewer
                    archivePath={archivePath}
                    descriptions={imageDescriptions}
                    onImageIndexChange={setActiveImageIdx}
                    isProcessing={isProcessing}
                  />
                </div>
              ) : isTextNote ? (
                <div className="h-full min-h-40 overflow-y-auto rounded-lg border bg-background p-5">
                  {noteText ? (
                    <NoteMarkdown content={noteText} archivePath={archivePath} sep={sep} />
                  ) : (
                    <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                      暂无正文
                    </div>
                  )}
                </div>
              ) : mediaUrl ? (
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
              {!isNoteContent && subtitles.length > 0 && (
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
                    {isImageNote && !isArticleNote && <TabsTrigger value="source">原帖</TabsTrigger>}
                    {!isPureWebpage && (
                      <TabsTrigger value="transcript">
                        {isImageNote ? "图片" : isTextNote ? "正文" : "字幕"}
                        {!isNoteContent && transcript && !isPolished && isProcessing && (
                          <span className="ml-1 text-[10px] text-amber-600">(原始)</span>
                        )}
                      </TabsTrigger>
                    )}
                    {(mindmap || isProcessing) && <TabsTrigger value="mindmap">导图</TabsTrigger>}
                    {detail && <TabsTrigger value="detail">详情</TabsTrigger>}
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

                {isImageNote && (
                  <TabsContent value="source" className="mt-3 relative flex-1">
                    <div className="absolute inset-0 overflow-y-auto rounded-md border p-5 text-sm leading-7">
                      {noteText ? (
                        <NoteMarkdown content={noteText} archivePath={archivePath} sep={sep} />
                      ) : isProcessing ? (
                        <div className="flex h-full items-center justify-center text-muted-foreground">
                          <HugeiconsIcon icon={Loading03Icon} className="h-4 w-4 animate-spin mr-2" />
                          <span className="text-sm">等待原帖正文...</span>
                        </div>
                      ) : (
                        <div className="flex h-full items-center justify-center text-muted-foreground text-sm">
                          暂无原帖正文
                        </div>
                      )}
                    </div>
                  </TabsContent>
                )}

                {!isPureWebpage && (
                  <TabsContent value="transcript" className="mt-3 relative flex-1">
                    <div className="absolute inset-0 rounded-md border flex flex-col">
                      {isImageNote ? (
                        imageDescriptions.length > 0 ? (
                          <div className="overflow-y-auto flex-1 p-3 space-y-3">
                            {imageDescriptions.map((d) => (
                              <div
                                key={d.index}
                                className={cn(
                                  "rounded-md border p-2 cursor-pointer transition-colors text-sm",
                                  activeImageIdx === d.index ? "border-primary bg-primary/5" : "hover:bg-muted/30",
                                )}
                                onClick={() => setActiveImageIdx(d.index)}
                              >
                                <div className="flex items-center gap-1.5 mb-1">
                                  <span className="text-[10px] font-medium text-muted-foreground tabular-nums">
                                    图片 {d.index + 1}
                                  </span>
                                  {d.kind === "text" && (
                                    <span className="rounded bg-sky-500/10 px-1 text-[9px] text-sky-600 dark:text-sky-400">文字</span>
                                  )}
                                </div>
                                {d.text ? (
                                  <p className="text-xs leading-relaxed whitespace-pre-wrap">{d.text}</p>
                                ) : (
                                  <p className="text-xs text-muted-foreground italic">无描述</p>
                                )}
                              </div>
                            ))}
                          </div>
                        ) : isProcessing ? (
                          <div className="flex items-center justify-center h-full text-muted-foreground">
                            <HugeiconsIcon icon={Loading03Icon} className="h-4 w-4 animate-spin mr-2" />
                            <span className="text-sm">正在分析图片...</span>
                          </div>
                        ) : (
                          <div className="flex items-center justify-center h-full text-muted-foreground text-sm">无图片数据</div>
                        )
                      ) : isTextNote ? (
                        noteText ? (
                          <div className="overflow-y-auto flex-1 p-5 text-sm leading-7">
                            <NoteMarkdown content={noteText} archivePath={archivePath} sep={sep} />
                          </div>
                        ) : (
                          <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
                            暂无正文
                          </div>
                        )
                      ) : (
                        <>
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
                              currentTime={currentTime}
                              tocTree={mindmapTree}
                              onTocSeek={seekTo}
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
                        </>
                      )}
                    </div>
                  </TabsContent>
                )}

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
                {detail && (
                  <TabsContent value="detail" className="mt-3 relative flex-1">
                    <div className="absolute inset-0 rounded-md border">
                      <SummaryTab content={detail} />
                    </div>
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
