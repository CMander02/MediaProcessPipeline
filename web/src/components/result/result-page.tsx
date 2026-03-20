import { useCallback, useEffect, useState } from "react"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useArchives, type ArchiveItem } from "@/hooks/use-archives"
import { useMediaSync } from "@/hooks/use-media-sync"
import { parseSRT, type Subtitle } from "@/lib/srt"
import { ArchivePicker } from "./archive-picker"
import { MediaPlayer } from "./media-player"
import { SpeakerPanel } from "./speaker-panel"
import { SpeakerTimeline } from "./speaker-timeline"
import { TranscriptTab } from "./transcript-tab"
import { SummaryTab } from "./summary-tab"
import { AnalysisBadges } from "./analysis-badges"
import { Loader2 } from "lucide-react"

interface ArchiveContent {
  summary: string
  srt: string
  polished: string
  analysis: ArchiveItem["analysis"]
}

export function ResultPage() {
  const { archives, loading: archivesLoading } = useArchives()
  const [selectedPath, setSelectedPath] = useState("")
  const [selectedArchive, setSelectedArchive] = useState<ArchiveItem | null>(null)
  const [content, setContent] = useState<ArchiveContent | null>(null)
  const [subtitles, setSubtitles] = useState<Subtitle[]>([])
  const [loading, setLoading] = useState(false)

  const { bindMedia, currentTime, duration, currentSegmentIndex, autoScroll, seekTo, onManualScroll } =
    useMediaSync({ subtitles })

  const loadArchive = useCallback(
    async (path: string) => {
      const archive = archives.find((a) => a.path === path)
      if (!archive) return

      setSelectedPath(path)
      setSelectedArchive(archive)
      setLoading(true)

      try {
        const sep = path.includes("\\") ? "\\" : "/"
        const [summary, srt, polished] = await Promise.all([
          fetchFileContent(path + sep + "summary.md"),
          fetchFileContent(path + sep + "transcript.srt"),
          fetchFileContent(path + sep + "transcript_polished.md"),
        ])

        setContent({
          summary,
          srt,
          polished,
          analysis: archive.analysis,
        })

        // Parse SRT — prefer the polished SRT if it's actually SRT format
        const srtContent = srt || ""
        setSubtitles(srtContent ? parseSRT(srtContent) : [])
      } catch {
        setContent({ summary: "加载失败", srt: "", polished: "", analysis: {} })
        setSubtitles([])
      } finally {
        setLoading(false)
      }
    },
    [archives],
  )

  // Auto-select first archive
  useEffect(() => {
    if (archives.length > 0 && !selectedPath) {
      loadArchive(archives[0].path)
    }
  }, [archives, selectedPath, loadArchive])

  // Determine media type and URL
  const mediaUrl = selectedArchive?.media_file
    ? `/api/filesystem/media?path=${encodeURIComponent(selectedArchive.media_file)}`
    : null
  const mediaType = selectedArchive?.has_video ? "video" : "audio"

  return (
    <div className="space-y-4">
      {/* Archive picker */}
      <ArchivePicker
        archives={archives}
        selectedPath={selectedPath}
        onSelect={loadArchive}
      />

      {archivesLoading && (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      )}

      {loading && (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          <span className="ml-2 text-sm text-muted-foreground">加载中...</span>
        </div>
      )}

      {content && !loading && selectedArchive && (
        <div className="grid grid-cols-1 lg:grid-cols-[400px_1fr] gap-6">
          {/* Left column — sticky on desktop */}
          <div className="space-y-4 lg:sticky lg:top-4 lg:self-start">
            {/* Media player */}
            {mediaUrl && (
              <MediaPlayer
                src={mediaUrl}
                type={mediaType}
                bindMedia={bindMedia}
              />
            )}

            {/* Speaker panel */}
            <SpeakerPanel subtitles={subtitles} />

            {/* Speaker timeline */}
            <SpeakerTimeline
              subtitles={subtitles}
              duration={duration}
              currentTime={currentTime}
              onSeek={seekTo}
            />

            {/* Analysis badges */}
            <AnalysisBadges analysis={content.analysis} />
          </div>

          {/* Right column — tabbed content */}
          <div className="min-w-0">
            <Tabs defaultValue="summary">
              <TabsList>
                <TabsTrigger value="summary">摘要</TabsTrigger>
                <TabsTrigger value="transcript">字幕</TabsTrigger>
                <TabsTrigger value="polished">润色</TabsTrigger>
              </TabsList>

              <TabsContent value="summary" className="mt-3">
                <div className="rounded-md border h-[600px]">
                  <SummaryTab content={content.summary} />
                </div>
              </TabsContent>

              <TabsContent value="transcript" className="mt-3">
                <div className="rounded-md border h-[600px]">
                  <TranscriptTab
                    subtitles={subtitles}
                    currentSegmentIndex={currentSegmentIndex}
                    autoScroll={autoScroll}
                    onSegmentClick={(sub) => seekTo(sub.startTime)}
                    onManualScroll={onManualScroll}
                  />
                </div>
              </TabsContent>

              <TabsContent value="polished" className="mt-3">
                <div className="rounded-md border h-[600px]">
                  <SummaryTab content={content.polished} />
                </div>
              </TabsContent>
            </Tabs>
          </div>
        </div>
      )}

      {!archivesLoading && archives.length === 0 && (
        <p className="text-sm text-muted-foreground text-center py-12">
          暂无归档结果。处理完成后结果将显示在此处。
        </p>
      )}
    </div>
  )
}

async function fetchFileContent(fullPath: string): Promise<string> {
  try {
    const res = await fetch(
      `/api/filesystem/read?path=${encodeURIComponent(fullPath)}`,
    )
    if (!res.ok) return ""
    const data = await res.json()
    return data.content ?? ""
  } catch {
    return ""
  }
}
