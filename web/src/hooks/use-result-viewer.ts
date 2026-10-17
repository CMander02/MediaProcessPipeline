import { usePlatform } from "@/platform/use-platform"
import { notifyError, notifySuccess } from "@/lib/notify"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { createArchiveRepository } from "@/repositories/archive-repository"
import { useAppAccess } from "@/hooks/use-app-access-context"
import { useArchives, type ArchiveItem } from "@/hooks/use-archives"
import { usePreferences } from "@/hooks/use-preferences"
import { libraryChanged } from "@/hooks/use-library-directory"
import { type TranscriptTocNode } from "@/components/result/transcript-tab"
import { parseSRT, subtitlesToMarkdown, subtitlesToPlainText, subtitlesToSRT, type Subtitle, type SpeakerHints, type SpeakerQuality } from "@/lib/srt"
import { createZip, downloadBytes, safeFileName, type ZipEntry } from "@/lib/zip"
import { type ImageDescription } from "@/components/result/image-note-viewer"
import { api, type Task, type TaskFlowSnapshot, type TaskTimelineEvent } from "@/lib/api"
import { useViewPosition } from "@/hooks/use-view-position"
import { useMediaSync } from "@/hooks/use-media-sync"
import { type SpeakerMergeInfo } from "@/components/speaker-merge-dialog"
import { useTaskSSE, type FileReadyEvent, type StepEvent } from "@/hooks/use-task-sse"
import { navigate } from "@/lib/router"
import { Video01Icon, MusicNote01Icon, Image01Icon, Note01Icon } from "@hugeicons/core-free-icons"
import { buildChapterTree, parseChapterTimeline } from "../lib/result-chapters"
import {
  alignSecondary,
  carrySpeakers,
  DEFAULT_TRACK,
  languageName,
  parseSubtitleFile,
  type SubtitleTrackInfo,
} from "@/lib/subtitle-tracks"
import { timelineEventKey, timelineStatusText } from "../lib/result-timeline"
import {
  resolveSourceUrl,
  asRecord,
  normalizeArchivePath,
  firstHttpUrl,
  firstTextValue,
} from "../lib/result-metadata"
import { usePortraitResultLayout, useMobileResultLayout } from "./use-result-layout"


const EMPTY_SPEAKER_HINTS: SpeakerHints = { nameSources: {}, suggestions: [], participants: [] }

export interface ResultViewerProps {
  archivePath: string
  embedded?: boolean
  layoutWidth?: number
  onTarget?: (path: string | null, taskId?: string | null) => void
  taskId?: string | null
  /** Open the transcript at this second instead of where it was last left */
  startAt?: number
}

export function useResultViewer({ archivePath, taskId: taskIdProp, startAt, embedded, layoutWidth, onTarget }: ResultViewerProps) {
  const platformAdapter = usePlatform()
  const [scopeRevision, setScopeRevision] = useState(0)
  useEffect(() => {
    const changed = () => setScopeRevision((value) => value + 1)
    window.addEventListener("mpp:offline-scope-change", changed)
    return () => window.removeEventListener("mpp:offline-scope-change", changed)
  }, [])
  const archiveRepository = useMemo(() => createArchiveRepository(platformAdapter), [platformAdapter])
  const { capabilities, online } = useAppAccess()
  const { archives, refresh: refreshArchives } = useArchives(true, !embedded)
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
  const [transcriptEditing, setTranscriptEditing] = useState(false)
  const [renamingSpeaker, setRenamingSpeaker] = useState(false)
  const speakerWritePending = useRef(false)
  const speakerEditVersion = useRef(0)
  const [subtitleTracks, setSubtitleTracks] = useState<SubtitleTrackInfo[]>([])
  const [activeTrackLang, setActiveTrackLang] = useState<string | null>(null)
  const [polishedLang, setPolishedLang] = useState<string | null>(null)
  // A second language shown under each line, and the default track kept for its speakers.
  const [secondaryTrackLang, setSecondaryTrackLang] = useState<string | null>(null)
  const [secondarySubtitles, setSecondarySubtitles] = useState<Subtitle[] | null>(null)
  const primarySubtitles = useRef<Subtitle[]>([])
  const [speakerSubtitles, setSpeakerSubtitles] = useState<Subtitle[]>([])
  const [speakerQuality, setSpeakerQuality] = useState<SpeakerQuality | null>(null)
  const [derivedContentStale, setDerivedContentStale] = useState(false)
  const [speakerHints, setSpeakerHints] = useState<SpeakerHints>(EMPTY_SPEAKER_HINTS)
  /** Outcome of the last "补全人物" run, from the task record */
  const [speakerAutoFill, setSpeakerAutoFill] = useState<{ status: string; error?: string } | null>(null)
  const [autoFilling, setAutoFilling] = useState(false)
  const [regenerating, setRegenerating] = useState(false)
  const speakerLabelsKnown = useRef(false)
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
  const viewerGeneration = useRef(0)

  useEffect(() => {
    viewerGeneration.current += 1
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
    setSpeakerSubtitles([])
    setSpeakerQuality(null)
    setDerivedContentStale(false)
    setSpeakerHints(EMPTY_SPEAKER_HINTS)
    setSpeakerAutoFill(null)
    speakerLabelsKnown.current = false
    primarySubtitles.current = []
    setTranscriptEditing(false)
    setRenamingSpeaker(false)
    speakerWritePending.current = false
    speakerEditVersion.current += 1
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
    return () => { viewerGeneration.current += 1 }
  }, [archivePath, taskIdProp, scopeRevision])

  const transcriptChanged = useMemo(
    () => subtitlesToSRT(subtitles) !== subtitlesToSRT(parseSRT(transcript ?? "", { speakerLabels: isPolished || speakerLabelsKnown.current })),
    [subtitles, transcript, isPolished],
  )
  const hasUnsavedTranscript = transcriptEditing || transcriptChanged
  const unsavedTranscriptRef = useRef(hasUnsavedTranscript)
  unsavedTranscriptRef.current = hasUnsavedTranscript

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
      initialTime: startAt ?? savedPos.current.mediaTime,
      onTimeUpdate: updateMediaTime,
    })

  // A search result opens the transcript at its line, also when this archive is already open.
  useEffect(() => {
    if (startAt === undefined) return
    setActiveTab("transcript")
    seekTo(startAt * 1000)
  }, [archivePath, seekTo, startAt])

  // Chapters with their sections and sub-sections (from the mindmap's timed points).
  const chapterTocNodes = useMemo(
    () => buildChapterTree(sourceChapterNodes ?? summaryChapterNodes, mindmapTree, duration),
    [duration, mindmapTree, sourceChapterNodes, summaryChapterNodes],
  )

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
    async (oldName: string, newName: string, persist = true) => {
      const updated = subtitles.map((sub) =>
        sub.speaker === oldName ? { ...sub, speaker: newName } : sub,
      )
      const srtPath = archivePath + sep + (isPolished ? "transcript_polished.srt" : "transcript.srt")
      const content = subtitlesToSRT(updated)
      if (persist) {
        const saved = await api.filesystem.write(srtPath, content, transcript ?? undefined)
        if (!saved.success) throw new Error("字幕保存失败")
      }
      setSubtitles(updated)
      setSpeakerSubtitles(updated)
      primarySubtitles.current = updated
      setTranscript(content)
      setDerivedContentStale(true)
    },
    [subtitles, archivePath, sep, isPolished, transcript],
  )

  const handleRenameSpeaker = async (oldName: string, newName: string) => {
    if (oldName.toLowerCase() === "unknown") return
    if (speakerWritePending.current || hasUnsavedTranscript || mergeInfo) return
    const request = speakerEditVersion.current
    speakerWritePending.current = true
    setRenamingSpeaker(true)
    try {
      if (!resolvedTaskId) {
        await applyRenameLocally(oldName, newName)
        return
      }
      const res = await api.voiceprints.renameTaskSpeaker(resolvedTaskId, oldName, newName, "ask", transcript ?? undefined)
      if (request !== speakerEditVersion.current) return
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
      await applyRenameLocally(oldName, appliedName, false)
    } catch (err) {
      if (request !== speakerEditVersion.current) return
      notifyError("改名失败，请刷新字幕后重试", err)
    } finally {
      if (request === speakerEditVersion.current) {
        speakerWritePending.current = false
        setRenamingSpeaker(false)
      }
    }
  }

  const resolveMerge = async (choice: "merge" | "new" | "cancel") => {
    if (speakerWritePending.current) return
    if (!mergeInfo || !resolvedTaskId) {
      setMergeInfo(null)
      return
    }
    if (choice === "cancel") {
      setMergeInfo(null)
      return
    }
    const request = speakerEditVersion.current
    speakerWritePending.current = true
    setRenamingSpeaker(true)
    try {
      const res = await api.voiceprints.renameTaskSpeaker(
        resolvedTaskId,
        mergeInfo.oldName,
        mergeInfo.newName,
        choice,
        transcript ?? undefined,
      )
      if (request !== speakerEditVersion.current) return
      const appliedName = res.person_name ?? mergeInfo.newName
      await applyRenameLocally(mergeInfo.oldName, appliedName, false)
    } catch (err) {
      console.warn("Conflict resolution failed:", err)
    } finally {
      if (request === speakerEditVersion.current) {
        speakerWritePending.current = false
        setRenamingSpeaker(false)
        setMergeInfo(null)
      }
    }
  }

  const applyMetadataState = useCallback((metadata: Record<string, unknown>) => {
    setSourceUrl(resolveSourceUrl(metadata))
    setPlatform((metadata.platform as string | null) ?? null)
    setUploader((metadata.uploader as string | null) ?? null)
    setContentSubtype((metadata.content_subtype as string | null) ?? null)
    setNoteText((metadata.description as string | null) ?? null)

    const extra = asRecord(metadata.extra)
    setSpeakerQuality((asRecord(extra?.speaker_quality) as SpeakerQuality | null) ?? { status: "needs_review", reason: "旧档人物标签待核对" })
    setDerivedContentStale(extra?.derived_content_stale === true)
    setSpeakerHints({
      nameSources: (asRecord(extra?.speaker_name_sources) as Record<string, string> | null) ?? {},
      suggestions: Array.isArray(extra?.speaker_suggestions) ? extra.speaker_suggestions as SpeakerHints["suggestions"] : [],
      participants: [
        ...(Array.isArray(extra?.speaker_participants) ? extra.speaker_participants as string[] : []),
        ...(typeof metadata.uploader === "string" && metadata.uploader ? [metadata.uploader] : []),
      ].filter((name, index, names) => names.indexOf(name) === index),
    })
    speakerLabelsKnown.current = extra?.raw_speaker_label_contract === 1 || extra?.subtitle_source === "asr" || metadata.subtitle_source === "asr"
    const tracks = (extra?.subtitle_tracks as SubtitleTrackInfo[] | undefined) ?? []
    setSubtitleTracks(tracks)
    const polished = tracks.find((t) => t.polished)
    setPolishedLang(polished?.lang ?? null)
    if (polished) setActiveTrackLang((current) => current ?? polished.lang)
    const actualSource = extra?.subtitle_source ?? metadata.subtitle_source
    if (actualSource === "asr" || actualSource === "platform") setSubtitleSourceType(actualSource)
    else if (extra?.speaker_source === "subtitle_inference") setSubtitleSourceType("platform")
    else if (polished?.type === "asr") setSubtitleSourceType("asr")
    else if (polished) setSubtitleSourceType("platform")
    else if (tracks.length && tracks.every((t) => t.type !== "asr")) setSubtitleSourceType("platform")
    else setSubtitleSourceType(null)
  }, [])

  const applyTaskSnapshot = useCallback((task: Task) => {
    setTaskStatus(task.status)
    const repair = asRecord(asRecord(task.result)?.speaker_repair)
    setSpeakerAutoFill(repair && typeof repair.status === "string"
      ? { status: repair.status, error: typeof repair.error === "string" ? repair.error : undefined } : null)
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
    const generation = viewerGeneration.current
    try {
      const item = await archiveRepository.get(archivePath)
      if (!item || generation !== viewerGeneration.current) return null
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
  }, [archivePath, applyArchiveSnapshot, archiveRepository, scopeRevision])

  // Find archive from list
  useEffect(() => {
    if (embedded) return
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
  }, [archives, archive, archivePath, taskIdProp, resolvedTaskId, applyMetadataState, embedded])

  // Resolve media URL
  const resolveMediaUrl = useCallback((arch: ArchiveItem | null) => {
    if (!arch?.media_file || !online) {
      setMediaUrl(null)
      return
    }
    const url = api.filesystem.mediaUrl(arch.media_file)
    // Subtitle and speaker edits also change the archive revision. Keep the media
    // source stable for those edits; corrected durations still refresh old media.
    const revision = arch.duration_seconds ?? arch.metadata?.duration_seconds
    setMediaUrl(revision == null ? url : `${url}${url.includes("?") ? "&" : "?"}revision=${encodeURIComponent(String(revision))}`)
  }, [online])

  useEffect(() => {
    resolveMediaUrl(archive)
  }, [archive, resolveMediaUrl])

  // --- Progressive file loading ---
  const readFilePath = useCallback(async (path: string): Promise<string> => {
    const generation = viewerGeneration.current
    try {
      const content = await archiveRepository.readFile(path)
      return generation === viewerGeneration.current ? content : ""
    } catch {
      return ""
    }
  }, [archiveRepository])

  const loadFile = useCallback(async (filename: string, basePath = archivePath): Promise<string> => {
    const baseSep = basePath.includes("\\") ? "\\" : "/"
    return readFilePath(basePath + baseSep + filename)
  }, [archivePath, readFilePath])

  const applyTranscriptContent = useCallback((content: string, polished: boolean) => {
    if (unsavedTranscriptRef.current) {
      notifyError("字幕已有新版本，当前草稿已保留。请复制草稿后刷新。")
      return
    }
    const parsed = parseSRT(content, { speakerLabels: polished || speakerLabelsKnown.current })
    primarySubtitles.current = parsed
    setSpeakerSubtitles(parsed)
    setTranscript(content)
    setIsPolished(polished)
    setSubtitles(parsed)
  }, [])

  const loadGeneratedContent = useCallback(async (basePath = archivePath) => {
    const generation = viewerGeneration.current
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

    if (generation !== viewerGeneration.current) return
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
    const generation = viewerGeneration.current
    try {
      const task = await api.tasks.get(resolvedTaskId)
      if (generation !== viewerGeneration.current) return null
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
    const generation = viewerGeneration.current
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
      if (generation === viewerGeneration.current && resultDescriptions.length > 0) {
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
    if (generation === viewerGeneration.current && merged.length > 0) {
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
  }, [archivePath, loadGeneratedContent, scopeRevision])

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
      const generation = viewerGeneration.current
      setTaskStatus("completed")
      const task = await refreshTaskSnapshot()
      if (generation !== viewerGeneration.current) return
      const outputDir =
        data.output_dir ??
        (task?.result?.output_dir as string | undefined) ??
        ((task?.result?.archive as Record<string, unknown> | undefined)?.output_dir as string | undefined)

      await Promise.all([
        refreshArchives(true),
        refreshArchiveDetail(true),
        loadGeneratedContent(outputDir || archivePath),
      ])

      if (generation !== viewerGeneration.current) return
      if (outputDir && normalizeArchivePath(outputDir) !== normalizeArchivePath(archivePath)) {
        const tid = resolvedTaskId ? `&taskId=${encodeURIComponent(resolvedTaskId)}` : ""
        if (onTarget) onTarget(outputDir, resolvedTaskId)
        else navigate(`#/result/archive?path=${encodeURIComponent(outputDir)}${tid}`, { replace: true })
      }
    },
    onFailed(data) {
      setTaskStatus("failed")
      setTaskError(data.error ?? "处理失败")
    },
  })

  const trackFile = useCallback(async (track: SubtitleTrackInfo) => (
    track.polished ? loadFile("transcript_polished.srt") : loadFile(track.filename)
  ), [loadFile])

  // Platform tracks keep the platform's format (YouTube json3 under an .srt name); our own
  // recognition marks speakers in brackets.
  const readTrackLines = useCallback(async (track: SubtitleTrackInfo) => {
    const content = await trackFile(track)
    const lines = track.type === "asr" ? parseSRT(content) : parseSubtitleFile(content)
    if (!lines.length) notifyError(`${languageName(track.lang)}字幕读不出内容`, track.filename)
    return lines
  }, [trackFile])

  const selectTrack = useCallback(async (lang: string) => {
    if (lang === DEFAULT_TRACK) {
      // Older archives list their platform tracks without marking the polished default.
      const polished = await loadFile("transcript_polished.srt")
      const content = polished || await loadFile("transcript.srt")
      if (!content) return
      const parsed = parseSRT(content, { speakerLabels: Boolean(polished) || speakerLabelsKnown.current })
      primarySubtitles.current = parsed
      setSpeakerSubtitles(parsed)
      setActiveTrackLang(null)
      setTranscript(content)
      setIsPolished(Boolean(polished))
      setSubtitles(parsed)
      return
    }
    const track = subtitleTracks.find((t) => t.lang === lang)
    if (!track) return
    if (track.polished) {
      const content = await trackFile(track)
      if (!content) return
      const parsed = parseSRT(content)
      primarySubtitles.current = parsed
      setSpeakerSubtitles(parsed)
      setActiveTrackLang(lang)
      setTranscript(content)
      setIsPolished(true)
      setSubtitles(parsed)
    } else {
      const parsed = await readTrackLines(track)
      if (!parsed.length) return
      // Other languages have no speakers of their own: borrow them from the default track.
      const lines = carrySpeakers(parsed, primarySubtitles.current)
      setActiveTrackLang(lang)
      setTranscript(subtitlesToSRT(lines))
      setIsPolished(false)
      setSubtitles(lines)
    }
    if (secondaryTrackLang === lang) {
      setSecondaryTrackLang(null)
      setSecondarySubtitles(null)
    }
  }, [loadFile, readTrackLines, secondaryTrackLang, subtitleTracks, trackFile])

  const selectSecondaryTrack = useCallback(async (lang: string | null) => {
    const track = lang ? subtitleTracks.find((t) => t.lang === lang) : undefined
    if (!track) {
      setSecondaryTrackLang(null)
      setSecondarySubtitles(null)
      return
    }
    const lines = track.polished ? parseSRT(await trackFile(track)) : await readTrackLines(track)
    if (!lines.length) return
    setSecondaryTrackLang(track.lang)
    setSecondarySubtitles(lines)
  }, [readTrackLines, subtitleTracks, trackFile])

  const shownTrackLang = activeTrackLang ?? polishedLang
  // Edits and speaker renames go to the default (polished) track only.
  const isPrimaryTrack = !activeTrackLang || activeTrackLang === polishedLang
    || Boolean(subtitleTracks.find((t) => t.lang === activeTrackLang)?.polished)
  useEffect(() => {
    if (isPrimaryTrack) primarySubtitles.current = subtitles
  }, [isPrimaryTrack, subtitles])
  const secondaryTexts = useMemo(
    () => secondarySubtitles && secondaryTrackLang && secondaryTrackLang !== shownTrackLang
      ? alignSecondary(subtitles, secondarySubtitles)
      : null,
    [secondarySubtitles, secondaryTrackLang, shownTrackLang, subtitles],
  )

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
      libraryChanged()
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
      notifyError("下载失败", error)
    }
  }

  /** 导出 menu: the current tab as Markdown, subtitles as SRT or plain text, or everything as a zip. */
  const canExportZip = !platformAdapter.isNative
  const hasExportableContent = Boolean(summary || subtitles.length > 0 || mindmap || detail || noteText)
  const handleExport = async (kind: "markdown" | "srt" | "txt" | "zip") => {
    const baseName = safeFileName(displayTitle ?? "output", "output")
    try {
      if (kind === "markdown") {
        await handleDownload()
        return
      }
      if (kind === "srt") {
        await platformAdapter.saveTextFile(`${baseName}.srt`, subtitlesToSRT(subtitles))
        return
      }
      if (kind === "txt") {
        await platformAdapter.saveTextFile(`${baseName}.txt`, subtitlesToPlainText(subtitles))
        return
      }
      const entries: ZipEntry[] = []
      if (summary) entries.push({ name: "摘要.md", content: summary })
      if (subtitles.length > 0) {
        entries.push(
          { name: "字幕.md", content: getTranscriptMarkdown() },
          { name: "字幕.srt", content: subtitlesToSRT(subtitles) },
          { name: "字幕.txt", content: subtitlesToPlainText(subtitles) },
        )
      }
      if (mindmap) entries.push({ name: "导图.md", content: mindmap })
      if (detail) entries.push({ name: "视频详情.md", content: detail })
      if (noteText) entries.push({ name: `${safeFileName(sourceTabLabel || "正文")}.md`, content: noteText })
      if (entries.length === 0) return
      downloadBytes(`${baseName}.zip`, createZip(entries.map((entry) => ({ ...entry, name: `${baseName}/${entry.name}` }))))
    } catch (error) {
      notifyError("导出失败", error)
    }
  }

  const handleOpenSource = useCallback(() => {
    if (!sourceHref) return
    void platformAdapter.openExternal(sourceHref)
  }, [platformAdapter, sourceHref])

  const handleResumeFromCheckpoint = useCallback(async () => {
    if (resuming) return
    if (!resolvedTaskId) {
      notifyError("无法继续处理", "找不到这个条目的任务记录。")
      return
    }
    setResuming(true)
    try {
      const task = await api.tasks.get(resolvedTaskId)
      if (task.status !== "queued" && task.status !== "processing") {
        await api.tasks.checkpointRerun(resolvedTaskId)
      }
      await refreshArchives()
      if (onTarget) onTarget(null, resolvedTaskId)
      else navigate(`#/result/task/${resolvedTaskId}`)
    } catch (error) {
      notifyError("加入队列失败", error)
    } finally {
      setResuming(false)
    }
  }, [refreshArchives, resolvedTaskId, resuming, onTarget])

  /** Decide pending lines and name anonymous voices in the background; names set by hand stay. */
  const handleAutoFillSpeakers = useCallback(async () => {
    if (!resolvedTaskId || autoFilling) return
    setAutoFilling(true)
    try {
      const result = await api.voiceprints.repairTaskSpeakers(resolvedTaskId, "auto")
      setTaskStatus("queued")
      setSpeakerAutoFill({ status: "queued" })
      notifySuccess("已开始补全人物", result.message)
    } catch (error) {
      notifyError("补全人物没有开始", error)
    } finally {
      setAutoFilling(false)
    }
  }, [autoFilling, resolvedTaskId])

  /** Rewrite summary and mind map with the current names, staying on this page. */
  const handleRegenerateDerived = useCallback(async () => {
    if (!resolvedTaskId || regenerating) return
    setRegenerating(true)
    try {
      await api.tasks.checkpointRerun(resolvedTaskId)
      setTaskStatus("queued")
      notifySuccess("正在更新摘要和导图", "字幕和人物保持不变，完成后自动刷新。")
    } catch (error) {
      notifyError("更新摘要和导图失败", error)
    } finally {
      setRegenerating(false)
    }
  }, [regenerating, resolvedTaskId])

  const handleFullRerun = useCallback(async () => {
    if (rerunning) return
    setRerunning(true)
    try {
      if (!resolvedTaskId) {
        notifyError("无法重新处理", "找不到这个条目的任务记录。")
        return
      }
      await api.tasks.fullRerun(resolvedTaskId)
      await refreshArchives()
      if (onTarget) onTarget(null, resolvedTaskId)
      else navigate(`#/result/task/${resolvedTaskId}`)
    } catch (error) {
      notifyError("重新处理失败", error)
    } finally {
      setRerunning(false)
    }
  }, [refreshArchives, resolvedTaskId, rerunning, onTarget])

  const handleOpenLocalFolder = useCallback(async () => {
    if (openingFolder) return
    setOpeningFolder(true)
    try {
      await api.filesystem.openFolder(archivePath)
    } catch (error) {
      notifyError("打开本地文件夹失败", error)
    } finally {
      setOpeningFolder(false)
    }
  }, [archivePath, openingFolder])

  const portraitWindow = usePortraitResultLayout()
  const mobileWindow = useMobileResultLayout()
  const isPortraitLayout = layoutWidth === undefined ? portraitWindow : layoutWidth >= 560 && layoutWidth < 900
  const isMobileLayout = layoutWidth === undefined ? mobileWindow : layoutWidth < 560
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
    speakerSubtitles: isPrimaryTrack ? subtitles : speakerSubtitles,
    speakerQuality,
    derivedContentStale,
    speakerHints,
    speakerAutoFill,
    autoFilling,
    handleAutoFillSpeakers,
    regenerating,
    handleRegenerateDerived,
    duration,
    currentTime,
    seekTo,
    handleRenameSpeaker,
    renamingSpeaker,
    hasUnsavedTranscript,
    setTranscriptEditing,
    setTranscript,
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
    handleExport,
    canExportZip,
    hasExportableContent,
    summary,
    subtitleTracks,
    activeTrackLang,
    polishedLang,
    selectTrack,
    isPrimaryTrack,
    secondaryTrackLang,
    selectSecondaryTrack,
    secondaryTexts,
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
