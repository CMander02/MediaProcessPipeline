import { Button } from "@/components/ui/button"
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
  FolderOpenIcon,
  PlayIcon,
  RefreshIcon,
  Link01Icon,
  Gps01Icon,
  Note01Icon,
  ListTreeIcon,
} from "@hugeicons/core-free-icons"
import { MediaPlayer } from "@/components/result/media-player"
import { cn } from "@/lib/utils"
import { ImageNoteViewer } from "@/components/result/image-note-viewer"
import { SpeakerPanel } from "@/components/result/speaker-panel"
import { navigate } from "@/lib/router"
import { PlatformIcon } from "@/components/platform-icon"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Progress } from "@/components/ui/progress"
import { ResizablePanelGroup, ResizablePanel, ResizableHandle } from "@/components/ui/resizable"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { SummaryTab } from "@/components/result/summary-tab"
import { TranscriptTab } from "@/components/result/transcript-tab"
import { MindmapViewer } from "@/components/result/mindmap-viewer"
import { DeleteConfirmDialog } from "@/components/delete-confirm-dialog"
import { SpeakerMergeDialog } from "@/components/speaker-merge-dialog"
import { type ResultViewerProps, useResultViewer } from "../../hooks/use-result-viewer"
import { ArticleNoteReader, NoteMarkdown } from "../result/note-content"
import { ResultSourcePane, ResultContentPane } from "../result/result-content-pane"
import {
  timelineEventKey,
  timelineStatusClass,
  timelineStatusText,
  timelineTime,
} from "../../lib/result-timeline"

export function ResultPageComplete({ archivePath, taskId: taskIdProp }: ResultViewerProps) {
  const view = useResultViewer({ archivePath, taskId: taskIdProp })
  const {
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
    editingTitle,
    titleDraft,
    setTitleDraft,
    commitTitle,
    setEditingTitle,
    displayTitle,
    startEditTitle,
    platform,
    sourceHref,
    handleOpenSource,
    uploader,
    headerMediaLabel,
    headerMediaIcon,
    summary,
    mindmap,
    subtitleSourceType,
    isPolished,
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
    activeTab,
    setActiveTab,
    updateActiveTab,
    showSourceTab,
    sourceTabLabel,
    isPureWebpage,
    isLongArticle,
    detail,
    mindmapFit,
    getTabContent,
    handleCopy,
    copied,
    handleDownload,
    subtitleTracks,
    activeTrackLang,
    polishedLang,
    selectTrack,
    currentSegmentIndex,
    autoScroll,
    chapterTocNodes,
    onManualScroll,
    setSubtitles,
    setMindmapFit,
    showDeleteDialog,
    resolvedTaskId,
    mergeInfo,
    resolveMerge,
  } = view


  const mediaPlayerBlock = mediaUrl ? (
    <div className="sticky top-0 z-10 space-y-2 bg-background pb-2">
      <div className="flex justify-end">
        <Button
          type="button"
          variant={videoLoop ? "default" : "outline"}
          size="icon-sm"
          onClick={toggleVideoLoop}
          aria-pressed={videoLoop}
          aria-label={videoLoop ? "关闭循环播放" : "开启循环播放"}
          title={videoLoop ? "关闭循环播放" : "开启循环播放"}
        >
          <HugeiconsIcon icon={RefreshIcon} className="h-4 w-4" />
        </Button>
      </div>
      <MediaPlayer
        src={mediaUrl}
        type={mediaType}
        bindMedia={bindMedia}
        subtitleSrt={transcript ?? undefined}
        loop={videoLoop}
        onLoopChange={setVideoLoop}
      />
    </div>
  ) : archive?.media_file && !online ? (
    <div className="flex min-h-32 items-center justify-center rounded-lg border bg-muted/20 px-4 text-center text-sm text-muted-foreground">
      连接服务器后播放音视频；摘要、字幕、导图和图文资料可继续离线阅读。
    </div>
  ) : null

  const mediaPane = (
    <div className={cn(
      "h-full min-h-0 space-y-3",
      isPortraitLayout || isMobileLayout ? "overflow-y-auto p-0" : "overflow-y-auto p-4",
    )}>
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
            descriptions={imageDescriptions}
            activeIndex={activeImageIdx}
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
      ) : mediaPlayerBlock ? (
        mediaPlayerBlock
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
  )
  const renderSourceTab = () => <ResultSourcePane view={view} />

  const contentPane = <ResultContentPane view={view} />


  return (
    <div className="flex h-full flex-col">
      {/* Top bar */}
      <div className="flex shrink-0 items-center gap-2 border-b px-2 py-1.5 sm:gap-3 sm:px-4 sm:py-2">
        <Button className="h-11 md:h-8" variant="ghost" size="sm" onClick={() => navigate("#/files")}>
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
            <span className="truncate text-sm font-medium md:hidden">{displayTitle}</span>
            <button
              className="group hidden min-w-0 items-center gap-1 truncate text-left text-sm font-medium transition-colors hover:text-primary md:flex"
              onClick={startEditTitle}
              title="点击编辑标题"
            >
              <span className="truncate">{displayTitle}</span>
              <HugeiconsIcon icon={PencilEdit01Icon} className="h-3 w-3 shrink-0 opacity-0 group-hover:opacity-50 transition-opacity" />
            </button>
            <div className="hidden shrink-0 items-center gap-1.5 text-muted-foreground sm:flex">
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
                      ? "text-foreground"
                      : "text-muted-foreground",
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
          <span className="text-xs text-foreground flex items-center gap-1">
            <HugeiconsIcon icon={Loading03Icon} className="h-3 w-3 animate-spin" />
            处理中
          </span>
        )}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button className="h-11 w-11 md:h-8 md:w-8" variant="ghost" size="icon-sm" aria-label="更多操作" title="更多操作">
              <HugeiconsIcon icon={MoreHorizontalIcon} className="h-4 w-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem disabled={resuming} onClick={handleResumeFromCheckpoint}>
              <HugeiconsIcon icon={resuming ? Loading03Icon : PlayIcon} className={resuming ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
              {resuming ? "续做中" : "断点续做"}
            </DropdownMenuItem>
            <DropdownMenuItem disabled={rerunning} onClick={handleFullRerun}>
              <HugeiconsIcon icon={rerunning ? Loading03Icon : RefreshIcon} className={rerunning ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
              {rerunning ? "重做中" : "完整重做"}
            </DropdownMenuItem>
            {(capabilities.open_local_folder || capabilities.archive_mutation) && <DropdownMenuSeparator className="hidden md:block" />}
            {capabilities.open_local_folder && (
              <DropdownMenuItem className="hidden md:flex" disabled={openingFolder} onClick={handleOpenLocalFolder}>
                <HugeiconsIcon icon={openingFolder ? Loading03Icon : FolderOpenIcon} className={openingFolder ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
                打开本地文件夹
              </DropdownMenuItem>
            )}
            {capabilities.archive_mutation && (
              <DropdownMenuItem
                className="hidden md:flex"
                variant="destructive"
                onClick={() => setShowDeleteDialog(true)}
              >
                <HugeiconsIcon icon={Delete01Icon} className="h-4 w-4" />
                删除
              </DropdownMenuItem>
            )}
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
                  <span className="text-foreground">{flowStatusLabel}</span>
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
                      isDone && "border-foreground/20 bg-muted text-foreground",
                      isCurrent && !isDone && "border-foreground bg-foreground text-background",
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
        {isMobileLayout ? (
          <div className="absolute inset-0 overflow-y-auto p-3">
            <div className="space-y-3">
              <section aria-label="媒体与说话人" className="h-[min(58dvh,36rem)] min-h-[22rem] overflow-hidden rounded-lg border bg-background p-3 [&_button]:min-h-11 [&_button]:min-w-11">
                {mediaPane}
              </section>
              <section aria-label="知识内容" className="h-[calc(100dvh-10rem)] min-h-[38rem] overflow-hidden rounded-lg border bg-background p-3 [&_button]:min-h-11 [&_button]:min-w-11">
                {contentPane}
              </section>
            </div>
          </div>
        ) : isPortraitLayout ? (
          <div className="absolute inset-0 overflow-hidden p-3">
            <div className="grid h-full min-h-0 grid-rows-[minmax(220px,42%)_minmax(0,1fr)] gap-3 sm:grid-cols-[minmax(260px,0.95fr)_minmax(300px,1fr)] sm:grid-rows-1">
              <div className="min-h-0 overflow-hidden">
                {mediaPane}
              </div>
              <div className="min-h-0 overflow-hidden">
                {contentPane}
              </div>
            </div>
          </div>
        ) : (
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
                    descriptions={imageDescriptions}
                    activeIndex={activeImageIdx}
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
              ) : mediaPlayerBlock ? (
                mediaPlayerBlock
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
          </ResizablePanel>
        </ResizablePanelGroup>
        )}
      </div>

      {/* Delete confirmation dialog */}
      <DeleteConfirmDialog
        open={showDeleteDialog}
        onOpenChange={setShowDeleteDialog}
        title={displayTitle ?? ""}
        archivePath={archivePath}
        taskId={resolvedTaskId ?? undefined}
        taskDelete={Boolean(isProcessing && resolvedTaskId)}
        onDeleted={() => navigate("#/files")}
      />

      {/* Speaker merge confirmation */}
      <SpeakerMergeDialog info={mergeInfo} onResolve={resolveMerge} />
    </div>
  )
}
