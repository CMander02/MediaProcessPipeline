/**
 * Full-viewport result viewer for completed archives.
 * Resizable left/right panels. Left column is sticky (doesn't scroll with right).
 */
import { useCallback, useEffect, useState } from "react"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Button } from "@/components/ui/button"
import {
  ResizablePanelGroup,
  ResizablePanel,
  ResizableHandle,
} from "@/components/ui/resizable"
import { useArchives, type ArchiveItem } from "@/hooks/use-archives"
import { useMediaSync } from "@/hooks/use-media-sync"
import { parseSRT, type Subtitle } from "@/lib/srt"
import { navigate } from "@/lib/router"
import { MediaPlayer } from "@/components/result/media-player"
import { SpeakerPanel } from "@/components/result/speaker-panel"
import { SpeakerTimeline } from "@/components/result/speaker-timeline"
import { TranscriptTab } from "@/components/result/transcript-tab"
import { SummaryTab } from "@/components/result/summary-tab"
import { AnalysisBadges } from "@/components/result/analysis-badges"
import { ArrowLeft, Loader2 } from "lucide-react"

interface ArchiveContent {
  summary: string
  srt: string
  polished: string
  analysis: ArchiveItem["analysis"]
}

export function ResultPageComplete({ archivePath }: { archivePath: string }) {
  const { archives } = useArchives()
  const [archive, setArchive] = useState<ArchiveItem | null>(null)
  const [content, setContent] = useState<ArchiveContent | null>(null)
  const [subtitles, setSubtitles] = useState<Subtitle[]>([])
  const [loading, setLoading] = useState(true)

  const { bindMedia, currentTime, duration, currentSegmentIndex, autoScroll, seekTo, onManualScroll } =
    useMediaSync({ subtitles })

  // Find archive from list or load directly
  useEffect(() => {
    const found = archives.find((a) => a.path === archivePath)
    if (found) setArchive(found)
  }, [archives, archivePath])

  const loadContent = useCallback(async (path: string) => {
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
        analysis: archive?.analysis ?? {},
      })
      setSubtitles(srt ? parseSRT(srt) : [])
    } catch {
      setContent({ summary: "加载失败", srt: "", polished: "", analysis: {} })
      setSubtitles([])
    } finally {
      setLoading(false)
    }
  }, [archive?.analysis])

  useEffect(() => {
    loadContent(archivePath)
  }, [archivePath, loadContent])

  // Update analysis when archive loads
  useEffect(() => {
    if (archive && content) {
      setContent((c) => c ? { ...c, analysis: archive.analysis } : c)
    }
  }, [archive])

  const mediaUrl = archive?.media_file
    ? `/api/filesystem/media?path=${encodeURIComponent(archive.media_file)}`
    : null
  const mediaType = archive?.has_video ? "video" : "audio"

  return (
    <div className="flex h-full flex-col">
      {/* Top bar */}
      <div className="shrink-0 flex items-center gap-3 px-4 py-2 border-b">
        <Button variant="ghost" size="sm" onClick={() => navigate("#/files")}>
          <ArrowLeft className="h-4 w-4 mr-1" />
          返回
        </Button>
        <h2 className="text-sm font-medium truncate flex-1">
          {archive?.title ?? archivePath.split(/[/\\]/).pop()}
        </h2>
      </div>

      {loading && (
        <div className="flex items-center justify-center flex-1">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          <span className="ml-2 text-sm text-muted-foreground">加载中...</span>
        </div>
      )}

      {content && !loading && (
        <div className="flex-1 min-h-0 relative">
          <ResizablePanelGroup
            orientation="horizontal"
            className="absolute inset-0"
          >
            {/* Left panel — media + info, scrolls independently */}
            <ResizablePanel defaultSize="35%" minSize="20%" maxSize="50%">
              <div className="h-full overflow-y-auto p-4 space-y-3">
                {mediaUrl && (
                  <div className="sticky top-0 z-10 bg-background pb-2">
                    <MediaPlayer src={mediaUrl} type={mediaType} bindMedia={bindMedia} />
                  </div>
                )}
                <SpeakerPanel subtitles={subtitles} />
                <SpeakerTimeline
                  subtitles={subtitles}
                  duration={duration}
                  currentTime={currentTime}
                  onSeek={seekTo}
                />
                <AnalysisBadges analysis={content.analysis} />
              </div>
            </ResizablePanel>

            <ResizableHandle withHandle />

            {/* Right panel — tabbed content */}
            <ResizablePanel defaultSize="65%" minSize="35%">
              <div className="h-full flex flex-col p-4">
                <Tabs defaultValue="summary" className="flex flex-col flex-1 min-h-0">
                  <TabsList className="shrink-0">
                    <TabsTrigger value="summary">摘要</TabsTrigger>
                    <TabsTrigger value="transcript">字幕</TabsTrigger>
                    <TabsTrigger value="polished">润色</TabsTrigger>
                  </TabsList>

                  <TabsContent value="summary" className="mt-3 flex-1 min-h-0">
                    <div className="rounded-md border h-full">
                      <SummaryTab content={content.summary} />
                    </div>
                  </TabsContent>

                  <TabsContent value="transcript" className="mt-3 flex-1 min-h-0">
                    <div className="rounded-md border h-full">
                      <TranscriptTab
                        subtitles={subtitles}
                        currentSegmentIndex={currentSegmentIndex}
                        autoScroll={autoScroll}
                        onSegmentClick={(sub) => seekTo(sub.startTime)}
                        onManualScroll={onManualScroll}
                      />
                    </div>
                  </TabsContent>

                  <TabsContent value="polished" className="mt-3 flex-1 min-h-0">
                    <div className="rounded-md border h-full">
                      <SummaryTab content={content.polished} />
                    </div>
                  </TabsContent>
                </Tabs>
              </div>
            </ResizablePanel>
          </ResizablePanelGroup>
        </div>
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
