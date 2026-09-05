import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { HugeiconsIcon } from "@hugeicons/react"
import {
  Tick02Icon,
  Copy01Icon,
  Download01Icon,
  Loading03Icon,
  Link01Icon,
  Gps01Icon,
} from "@hugeicons/core-free-icons"
import { ScrollArea } from "@/components/ui/scroll-area"
import { cn } from "@/lib/utils"
import { SummaryTab } from "@/components/result/summary-tab"
import { TranscriptTab } from "@/components/result/transcript-tab"
import { MindmapViewer } from "@/components/result/mindmap-viewer"
import { useResultViewer } from "../../hooks/use-result-viewer"
import { NoteMarkdown } from "./note-content"

export function ResultSourcePane({ view }: { view: ReturnType<typeof useResultViewer> }) {
  const { sourceHref, handleOpenSource, noteText, archivePath, sep, isProcessing, sourceTabLabel } = view
  return (
    <TabsContent value="source" className="mt-3 relative flex-1">
      <div className="absolute inset-0 flex flex-col overflow-hidden rounded-md border">
        <div className="flex min-h-11 shrink-0 items-center justify-between gap-2 border-b px-4 py-2">
          <Badge variant="secondary">来源原文</Badge>
          {sourceHref ? (
            <Button type="button" variant="ghost" size="sm" onClick={handleOpenSource}>
              <HugeiconsIcon icon={Link01Icon} data-icon="inline-start" />
              打开来源
            </Button>
          ) : null}
        </div>
        <ScrollArea className="min-h-0 flex-1">
          <div className="p-5 text-sm leading-7">
            {noteText ? (
              <NoteMarkdown content={noteText} archivePath={archivePath} sep={sep} />
            ) : isProcessing ? (
              <div className="flex min-h-40 items-center justify-center text-muted-foreground">
                <HugeiconsIcon icon={Loading03Icon} className="mr-2 size-4 animate-spin" />
                <span className="text-sm">等待{sourceTabLabel}内容…</span>
              </div>
            ) : (
              <div className="flex min-h-40 items-center justify-center text-sm text-muted-foreground">
                暂无{sourceTabLabel}内容
              </div>
            )}
          </div>
        </ScrollArea>
      </div>
    </TabsContent>
  )
}

export function ResultContentPane({ view }: { view: ReturnType<typeof useResultViewer> }) {
  const {
    isPortraitLayout,
    isMobileLayout,
    activeTab,
    setActiveTab,
    updateActiveTab,
    showSourceTab,
    sourceTabLabel,
    isPureWebpage,
    isLongArticle,
    isImageNote,
    isTextNote,
    isNoteContent,
    transcript,
    isPolished,
    isProcessing,
    mindmap,
    detail,
    mindmapFit,
    getTabContent,
    handleCopy,
    copied,
    handleDownload,
    summary,
    imageDescriptions,
    activeImageIdx,
    setActiveImageIdx,
    noteText,
    archivePath,
    sep,
    subtitleTracks,
    activeTrackLang,
    polishedLang,
    selectTrack,
    subtitles,
    currentSegmentIndex,
    autoScroll,
    seekTo,
    currentTime,
    chapterTocNodes,
    onManualScroll,
    setSubtitles,
    displayTitle,
    setMindmapFit,
  } = view
  const renderSourceTab = () => <ResultSourcePane view={view} />


  const contentPane = (
    <div className={cn(
      "h-full min-h-0 flex flex-col",
      isPortraitLayout || isMobileLayout ? "p-0" : "p-4",
    )}>
      <Tabs value={activeTab} onValueChange={(value) => { setActiveTab(value); updateActiveTab(value) }} className="flex flex-col flex-1 min-h-0">
        <div className="shrink-0 flex min-w-0 items-center gap-1.5">
          <div className="min-w-0 flex-1 overflow-x-auto pb-1">
          <TabsList className="w-max">
            <TabsTrigger value="summary">摘要</TabsTrigger>
            {showSourceTab && <TabsTrigger value="source">{sourceTabLabel}</TabsTrigger>}
            {!isPureWebpage && !isLongArticle && (
              <TabsTrigger value="transcript">
                {isImageNote ? "图片" : isTextNote ? "正文" : "字幕"}
                {!isNoteContent && transcript && !isPolished && isProcessing && (
                  <span className="ml-1 text-[10px] text-amber-600">(原始)</span>
                )}
              </TabsTrigger>
            )}
            {(mindmap || isProcessing) && <TabsTrigger value="mindmap">导图</TabsTrigger>}
            {detail && !isLongArticle && <TabsTrigger value="detail">详情</TabsTrigger>}
          </TabsList>
          </div>
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

        {showSourceTab ? renderSourceTab() : null}

        {!isPureWebpage && !isLongArticle && (
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
                      tocNodes={chapterTocNodes}
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
          <TabsContent value="mindmap" className="mt-3 relative flex-1 min-h-0">
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
        {detail && !isLongArticle && (
          <TabsContent value="detail" className="mt-3 relative flex-1">
            <div className="absolute inset-0 rounded-md border">
              <SummaryTab content={detail} />
            </div>
          </TabsContent>
        )}
      </Tabs>
    </div>
  )
  return contentPane
}
