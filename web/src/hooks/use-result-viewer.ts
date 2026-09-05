import { usePlatform } from "@/platform/use-platform"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { createArchiveRepository } from "@/repositories/archive-repository"
import { useAppAccess } from "@/hooks/use-app-access-context"
import { useArchives, type ArchiveItem } from "@/hooks/use-archives"
import { usePreferences } from "@/hooks/use-preferences"
import { type TranscriptTocNode } from "@/components/result/transcript-tab"
import { parseSRT, subtitlesToMarkdown, subtitlesToSRT, type Subtitle } from "@/lib/srt"
import { type ImageDescription } from "@/components/result/image-note-viewer"
import { api, type Task, type TaskFlowSnapshot, type TaskTimelineEvent } from "@/lib/api"
import { useViewPosition } from "@/hooks/use-view-position"
import { useMediaSync } from "@/hooks/use-media-sync"
import { type SpeakerMergeInfo } from "@/components/speaker-merge-dialog"
import { useTaskSSE, type FileReadyEvent, type StepEvent } from "@/hooks/use-task-sse"
import { navigate } from "@/lib/router"
import { Video01Icon, MusicNote01Icon, Image01Icon, Note01Icon } from "@hugeicons/core-free-icons"
import {
  collectLegacyMindmapChapters,
  completeChapterRanges,
  parseChapterTimeline,
} from "../lib/result-chapters"
import { timelineEventKey, timelineStatusText } from "../lib/result-timeline"
import {
  resolveSourceUrl,
  asRecord,
  normalizeArchivePath,
  firstHttpUrl,
  firstTextValue,
  resolveRerunSource,
} from "../lib/result-metadata"
import { usePortraitResultLayout, useMobileResultLayout } from "./use-result-layout"

interface SubtitleTrackInfo {
  lang: string
  type: string
  filename: string
  polished: boolean
}

export interface ResultViewerProps {
  archivePath: string
  taskId?: string | null
}

export function useResultViewer({ archivePath, taskId: taskIdProp }: ResultViewerProps) {
  const platformAdapter = usePlatform()
  const archiveRepository = useMemo(() => createArchiveRepository(platformAdapter), [platformAdapter])
  const { capabilities, online } = useAppAccess()
  const { archives, refresh: refreshArchives } = useArchives()
  const { prefs, update: updatePrefs } = usePreferences()
  const [archive, setArchive] = useState<ArchiveItem | null>(null)
  const [showDeleteDialog, setShowDeleteDialog] = useState(false)
  const [resuming, setResuming] = useState(false)
  const [rerunning, setRerunning] = useState(false)
  const [openingFolder, setOpeningFolder] = useState(false)
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
  const [mindmapTree, setMindmapTree] = useState<TranscriptTocNode | null>(null)
  const [sourceChapterNodes, setSourceChapterNodes] = useState<TranscriptTocNode[] | null>(null)
  const [summaryChapterNodes, setSummaryChapterNodes] = useState<TranscriptTocNode[] | null>(null)
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
    setSourceChapterNodes(null)
    setSummaryChapterNodes(null)
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

  const chapterTocNodes = useMemo(() => {
    const preferred = sourceChapterNodes ?? summaryChapterNodes ?? collectLegacyMindmapChapters(mindmapTree)
    return completeChapterRanges(preferred, duration)
  }, [duration, mindmapTree, sourceChapterNodes, summaryChapterNodes])

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
    [subtitles, archivePath, sep, isPolished],
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

  const applyArchiveSnapshot = useCallback((item: ArchiveItem) => {
    setArchive(item)
    const meta = (item.metadata || {}) as Record<string, unknown>
    applyMetadataState(meta)
    setResolvedTaskId((current) => current === undefined ? item.task_id ?? null : current)
    if (item.processing) {
      setTaskStatus("processing")
    } else if (!taskIdProp) {
      setTaskStatus("completed")
    }
  }, [applyMetadataState, taskIdProp])

  const refreshArchiveDetail = useCallback(async (silent = false) => {
    try {
      const item = await archiveRepository.get(archivePath)
      if (!item) return null
      applyArchiveSnapshot(item)
      return item
    } catch (error) {
      if (!silent) console.warn("Failed to load archive detail:", error)
      return null
    }
  }, [archivePath, applyArchiveSnapshot, archiveRepository])

  useEffect(() => {
    let cancelled = false
    archiveRepository.get(archivePath)
      .then((item) => {
        if (cancelled) return
        if (item) applyArchiveSnapshot(item)
      })
      .catch((error) => {
        if (!cancelled) console.warn("Failed to load archive detail:", error)
      })
    return () => {
      cancelled = true
    }
  }, [archivePath, applyArchiveSnapshot, archiveRepository])

  // Find archive from list
  useEffect(() => {
    const found = archives.find((a) => a.path === archivePath)
    if (found) {
      if (!archive || archive.path !== archivePath) {
        setArchive(found)
        const meta = (found.metadata || {}) as Record<string, unknown>
        applyMetadataState(meta)
      }
      if (resolvedTaskId === undefined) {
        setResolvedTaskId(found.task_id ?? null)
      }
      if (found.processing) {
        setTaskStatus("processing")
      } else if (!taskIdProp) {
        setTaskStatus("completed")
      }
    }
  }, [archives, archive, archivePath, taskIdProp, resolvedTaskId, applyMetadataState])

  // Resolve media URL
  const resolveMediaUrl = useCallback((arch: ArchiveItem | null) => {
    if (!arch?.media_file || !online) {
      setMediaUrl(null)
      return
    }
    setMediaUrl(api.filesystem.mediaUrl(arch.media_file))
  }, [online])

  useEffect(() => {
    resolveMediaUrl(archive)
  }, [archive, resolveMediaUrl])

  // --- Progressive file loading ---
  const readFilePath = useCallback(async (path: string): Promise<string> => {
    try {
      return await archiveRepository.readFile(path)
    } catch {
      return ""
    }
  }, [archiveRepository])

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
      sourceContextJson,
      summaryJson,
      detailMd,
      sourceMd,
      polishedSrt,
      rawSrt,
    ] = await Promise.all([
      loadFile("summary.md", basePath),
      loadFile("mindmap.md", basePath),
      loadFile("mindmap.json", basePath),
      loadFile("source_context.json", basePath),
      loadFile("summary.json", basePath),
      loadFile("detail.md", basePath),
      loadFile("source.md", basePath),
      loadFile("transcript_polished.srt", basePath),
      loadFile("transcript.srt", basePath),
    ])

    if (summaryMd) setSummary(summaryMd)
    if (mindmapMd) setMindmap(mindmapMd)
    if (mindmapJson) {
      try {
        setMindmapTree(JSON.parse(mindmapJson) as TranscriptTocNode)
      } catch (err) {
        console.warn("Failed to parse mindmap.json:", err)
      }
    }
    if (sourceContextJson) setSourceChapterNodes(parseChapterTimeline(sourceContextJson))
    if (summaryJson) setSummaryChapterNodes(parseChapterTimeline(summaryJson))
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
    if (!resolvedTaskId || !online) return null
    try {
      const task = await api.tasks.get(resolvedTaskId)
      applyTaskSnapshot(task)
      return task
    } catch {
      return null
    }
  }, [resolvedTaskId, online, applyTaskSnapshot])

  useEffect(() => {
    if (!resolvedTaskId || !online) return
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
  }, [resolvedTaskId, online, applyTaskSnapshot])

  // Load image descriptions for image_note content type
  const loadImageDescriptions = useCallback(async () => {
    if (!archivePath) return
    const resultDescriptions: ImageDescription[] = []
    if (archive?.offline) {
      const files = archiveRepository.listFiles(archive, "images")
        .filter((file) => /\.(?:jpe?g|png|webp|gif|bmp|avif)$/i.test(file.relativePath))
        .sort((a, b) => a.relativePath.localeCompare(b.relativePath, undefined, { numeric: true }))
      for (const file of files) {
        const stem = file.relativePath.split("/").pop()?.replace(/\.[^.]+$/, "") ?? ""
        const index = Number.parseInt(stem, 10)
        const text = await archiveRepository.readFile(`${archivePath}/descriptions/${stem}.md`)
        resultDescriptions.push({
          index: Number.isFinite(index) ? index : resultDescriptions.length,
          image_path: file.url,
          kind: "content",
          text,
        })
      }
      if (resultDescriptions.length > 0) {
        setImageDescriptions(resultDescriptions)
        setActiveImageIdx(resultDescriptions[0].index)
      }
      return
    }
    if (resolvedTaskId && online) {
      try {
        const task = await api.tasks.get(resolvedTaskId)
        const descs = task.result?.image_descriptions as ImageDescription[] | undefined
        if (descs && descs.length > 0) {
          resultDescriptions.push(...descs)
        }
      } catch {
        // Archive files provide the fallback source below.
      }
    }
    const resultByIndex = new Map(resultDescriptions.map((item) => [item.index, item]))
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
        try {
          text = (await api.filesystem.read(descPath)).content ?? ""
        } catch {
          // Description files are optional.
        }
        const index = Number.parseInt(stem, 10)
        const fromResult = Number.isFinite(index) ? resultByIndex.get(index) : undefined
        descs.push({
          ...(fromResult ?? {}),
          index: Number.isFinite(index) ? index : descs.length,
          image_path: item.path,
          kind: fromResult?.kind ?? "content",
          text: (fromResult?.text || text || ""),
        })
      }
    } catch {
      // Legacy numbered images provide the fallback source below.
    }
    for (let i = descs.length > 0 ? 30 : 0; i < 30; i++) {
      const imgPath = archivePath + sep + "images" + sep + `${String(i).padStart(2, "0")}.jpg`
      const descPath = archivePath + sep + "descriptions" + sep + `${String(i).padStart(2, "0")}.md`
      try {
        const check = await api.filesystem.read(imgPath)
        if (!check || !check.success) break
        let text = ""
        try {
          text = (await api.filesystem.read(descPath)).content ?? ""
        } catch {
          // Description files are optional.
        }
        const fromResult = resultByIndex.get(i)
        descs.push({
          ...(fromResult ?? {}),
          index: i,
          image_path: imgPath,
          kind: fromResult?.kind ?? "content",
          text: fromResult?.text || text,
        })
      } catch { break }
    }
    const merged = descs.length > 0 ? descs : resultDescriptions
    if (merged.length > 0) {
      const sorted = [...merged].sort((a, b) => a.index - b.index)
      setImageDescriptions(sorted)
      setActiveImageIdx((current) => sorted.some((item) => item.index === current) ? current : sorted[0].index)
    }
  }, [archivePath, sep, resolvedTaskId, online, archive, archiveRepository])

  useEffect(() => {
    // Wait until resolvedTaskId is known (undefined = not yet resolved, null/string = resolved)
    if (contentSubtype === "image_note" && resolvedTaskId !== undefined) loadImageDescriptions()
  }, [contentSubtype, loadImageDescriptions, resolvedTaskId])

  // Load files independently on mount
  useEffect(() => {
    loadGeneratedContent()
  }, [archivePath, loadGeneratedContent])

  // --- SSE subscription for in-progress tasks ---
  useTaskSSE(online ? resolvedTaskId : null, {
    // Snapshot is sent immediately on (re)connect — rebuilds pipeline state
    // when the user navigates back to the result page mid-processing.
    onSnapshot(data) {
      setTaskStatus(data.status)
      setCompletedSteps(data.completed_steps ?? [])
      if (data.flow) setTaskFlow(data.flow)
      if (data.error) setTaskError(data.error)
    },
    onStep(data: StepEvent) {
      setTaskStatus("processing")
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
      } else if (file === "summary.json") {
        loadReadyFile().then((c) => { if (c) setSummaryChapterNodes(parseChapterTimeline(c)) })
      } else if (file === "source_context.json") {
        loadReadyFile().then((c) => { if (c) setSourceChapterNodes(parseChapterTimeline(c)) })
      } else if (file === "mindmap.md") {
        loadReadyFile().then((c) => { if (c) setMindmap(c) })
      } else if (file === "mindmap.json") {
        loadReadyFile().then((c) => {
          if (!c) return
          try {
            setMindmapTree(JSON.parse(c) as TranscriptTocNode)
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
        refreshArchiveDetail(true)
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
        refreshArchiveDetail(true),
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

  const mediaType: "video" | "audio" = archive?.has_video ? "video" : "audio"
  const sourceHref = firstHttpUrl(sourceUrl)
  const isImageNote = contentSubtype === "image_note"
  const isTextNote = contentSubtype === "text_note"
  const archiveMetadata = (archive?.metadata || {}) as Record<string, unknown>
  const archiveExtra = asRecord(archiveMetadata.extra)
  const bilibiliType = typeof archiveExtra?.bilibili_type === "string" ? archiveExtra.bilibili_type : null
  const isArticleNote = platform === "bilibili_opus" && bilibiliType === "article"
  const isPureWebpage = platform === "webpage" && isTextNote
  const isLongArticle = archiveExtra?.content_kind === "long_article"
  const isNoteContent = isImageNote || isTextNote
  const headerMediaIcon = isLongArticle
    ? Note01Icon
    : isImageNote || archive?.has_image
    ? Image01Icon
    : isTextNote
      ? Note01Icon
    : mediaType === "video"
      ? Video01Icon
      : MusicNote01Icon
  const headerMediaLabel = isLongArticle
    ? "长文"
    : isImageNote || archive?.has_image
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
  const showSourceTab = (isImageNote && !isArticleNote) || (!isNoteContent && Boolean(noteText || isProcessing))
  const sourceTabLabel = isImageNote ? "原帖" : "简介"
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
    if (isLongArticle && activeTab !== "summary" && activeTab !== "mindmap") {
      setActiveTab("summary")
      updateActiveTab("summary")
    } else if (isPureWebpage && activeTab === "transcript") {
      setActiveTab("summary")
      updateActiveTab("summary")
    } else if (!showSourceTab && activeTab === "source") {
      setActiveTab("summary")
      updateActiveTab("summary")
    }
  }, [activeTab, isLongArticle, isPureWebpage, showSourceTab, updateActiveTab])

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
      await Promise.all([
        refreshArchives(true),
        refreshArchiveDetail(true),
      ])
    } catch {
      // ignore, revert
    }
    setEditingTitle(false)
  }

  const [copied, setCopied] = useState(false)

  const getTranscriptMarkdown = () => {
    const publishedAt = firstTextValue(
      archiveMetadata.upload_date,
      archiveMetadata.published_at,
      archiveMetadata.release_date,
    )
    return subtitlesToMarkdown(subtitles, {
      title: displayTitle || archive?.title || "字幕",
      source_url: sourceHref,
      platform,
      uploader,
      published_at: publishedAt,
      created_at: archive?.created_at,
      duration_seconds: archive?.duration_seconds ?? duration,
      language: archive?.analysis?.language,
      subtitle_language: activeTrackLang,
      subtitle_source: subtitleSourceType,
      polished: isPolished,
      task_id: resolvedTaskId,
    })
  }

  const getTabContent = () => {
    if (activeTab === "summary") return { content: summary, suffix: "摘要", ext: "md" }
    if (activeTab === "source" && showSourceTab) return { content: noteText, suffix: sourceTabLabel, ext: "md" }
    if (activeTab === "transcript" && isTextNote && !isPureWebpage) return { content: noteText, suffix: "正文", ext: "md" }
    if (activeTab === "transcript") {
      return {
        content: subtitles.length > 0 ? getTranscriptMarkdown() : null,
        suffix: "字幕",
        ext: "md",
      }
    }
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

  const handleDownload = async () => {
    const tab = getTabContent()
    if (!tab?.content) return
    const baseName = (displayTitle ?? "output").replace(/[/\\:*?"<>|]/g, "_")
    const filename = `${baseName}-${tab.suffix}.${tab.ext}`
    try {
      await platformAdapter.saveTextFile(filename, tab.content)
    } catch (error) {
      window.alert(`下载失败：${error instanceof Error ? error.message : String(error)}`)
    }
  }

  const handleOpenSource = useCallback(() => {
    if (!sourceHref) return
    void platformAdapter.openExternal(sourceHref)
  }, [platformAdapter, sourceHref])

  const handleResumeFromCheckpoint = useCallback(async () => {
    if (resuming) return
    if (!resolvedTaskId) {
      window.alert("找不到历史任务记录，无法断点续做。")
      return
    }
    setResuming(true)
    try {
      const task = await api.tasks.get(resolvedTaskId)
      if (task.status !== "queued" && task.status !== "processing") {
        await api.tasks.checkpointRerun(resolvedTaskId)
      }
      await refreshArchives()
      navigate(`#/result/task/${resolvedTaskId}`)
    } catch (error) {
      window.alert(`断点续做失败：${error instanceof Error ? error.message : String(error)}`)
    } finally {
      setResuming(false)
    }
  }, [refreshArchives, resolvedTaskId, resuming])

  const handleFullRerun = useCallback(async () => {
    if (rerunning) return
    setRerunning(true)
    try {
      let source = ""
      let options: Record<string, unknown> = {}
      if (resolvedTaskId) {
        try {
          const task = await api.tasks.get(resolvedTaskId)
          source = task.source
          options = task.options ?? {}
        } catch {
          source = ""
        }
      }
      if (!source) {
        source = resolveRerunSource((archive?.metadata ?? {}) as Record<string, unknown>, archive, sourceUrl)
      }
      if (!source) {
        window.alert("找不到原始来源，无法重做。")
        return
      }
      const task = await api.tasks.create(source, options)
      navigate(`#/result/task/${task.id}`)
    } catch (error) {
      window.alert(`完整重做失败：${error instanceof Error ? error.message : String(error)}`)
    } finally {
      setRerunning(false)
    }
  }, [archive, resolvedTaskId, rerunning, sourceUrl])

  const handleOpenLocalFolder = useCallback(async () => {
    if (openingFolder) return
    setOpeningFolder(true)
    try {
      await api.filesystem.openFolder(archivePath)
    } catch (error) {
      window.alert(`打开本地文件夹失败：${error instanceof Error ? error.message : String(error)}`)
    } finally {
      setOpeningFolder(false)
    }
  }, [archivePath, openingFolder])

  const isPortraitLayout = usePortraitResultLayout()
  const isMobileLayout = useMobileResultLayout()
  const videoLoop = Boolean(prefs.videoLoop)
  const toggleVideoLoop = useCallback(() => {
    updatePrefs({ videoLoop: !videoLoop })
  }, [updatePrefs, videoLoop])
  const setVideoLoop = useCallback((next: boolean) => {
    updatePrefs({ videoLoop: next })
  }, [updatePrefs])
  return {
    mediaUrl,
    videoLoop,
    toggleVideoLoop,
    mediaType,
    bindMedia,
    transcript,
    setVideoLoop,
    archive,
    online,
    isPortraitLayout,
    isMobileLayout,
    isArticleNote,
    noteText,
    archivePath,
    sep,
    imageDescriptions,
    isProcessing,
    isImageNote,
    activeImageIdx,
    setActiveImageIdx,
    isTextNote,
    isNoteContent,
    subtitles,
    duration,
    currentTime,
    seekTo,
    handleRenameSpeaker,
    sourceHref,
    handleOpenSource,
    sourceTabLabel,
    activeTab,
    setActiveTab,
    updateActiveTab,
    showSourceTab,
    isPureWebpage,
    isLongArticle,
    isPolished,
    mindmap,
    detail,
    mindmapFit,
    getTabContent,
    handleCopy,
    copied,
    handleDownload,
    summary,
    subtitleTracks,
    activeTrackLang,
    polishedLang,
    selectTrack,
    currentSegmentIndex,
    autoScroll,
    chapterTocNodes,
    onManualScroll,
    setSubtitles,
    displayTitle,
    setMindmapFit,
    editingTitle,
    titleDraft,
    setTitleDraft,
    commitTitle,
    setEditingTitle,
    startEditTitle,
    platform,
    uploader,
    headerMediaLabel,
    headerMediaIcon,
    subtitleSourceType,
    resuming,
    handleResumeFromCheckpoint,
    rerunning,
    handleFullRerun,
    capabilities,
    openingFolder,
    handleOpenLocalFolder,
    setShowDeleteDialog,
    showFlowDiagnostics,
    taskFlow,
    recentTimelineEvents,
    flowStatusLabel,
    flowProgress,
    flowCompletedSteps,
    latestStatusEvents,
    flowStepLabels,
    taskStatus,
    taskError,
    showDeleteDialog,
    resolvedTaskId,
    mergeInfo,
    resolveMerge,
  }
}
