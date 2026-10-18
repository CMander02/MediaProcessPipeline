import { useEffect, useLayoutEffect, useRef, useState } from "react"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { HugeiconsIcon } from "@hugeicons/react"
import {
  ArrowLeft01Icon,
  Tick02Icon,
  Loading03Icon,
  MoreHorizontalIcon,
  PencilEdit01Icon,
  Delete01Icon,
  FolderOpenIcon,
  PlayIcon,
  RefreshIcon,
  Link01Icon,
  Note01Icon,
  ListTreeIcon,
  Cancel01Icon,
  PauseIcon,
  PlayCircleIcon,
  AiMagicIcon,
} from "@hugeicons/core-free-icons"
import { MediaPlayer } from "@/components/result/media-player"
import { cn } from "@/lib/utils"
import { ImageNoteViewer } from "@/components/result/image-note-viewer"
import { SpeakerPanel } from "@/components/result/speaker-panel"
import { AttemptsMenu } from "@/components/result/attempts-menu"
import { buildHash, libraryHash, navigate, parseHash, setNavigationGuard } from "@/lib/router"
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
import { DeleteConfirmDialog } from "@/components/delete-confirm-dialog"
import { RerunConfirmDialog } from "@/components/rerun-confirm-dialog"
import { checkpointAction } from "@/lib/task-display"
import { SpeakerMergeDialog } from "@/components/speaker-merge-dialog"
import { type ResultViewerProps, useResultViewer } from "../../hooks/use-result-viewer"
import { ArticleNoteReader, NoteMarkdown } from "../result/note-content"
import { ResultContentPane } from "../result/result-content-pane"
import { MobileMediaOverview } from "../result/mobile-media-overview"
import { api } from "@/lib/api"
import { useRoute } from "@/lib/router"
import { usePreferences } from "@/hooks/use-preferences"
import {
  timelineEventKey,
  timelineStatusClass,
  timelineStatusText,
  timelineTime,
} from "../../lib/result-timeline"

export function ResultPageComplete({ archivePath, taskId: taskIdProp, startAt, embedded, onTarget, onDeleted }: ResultViewerProps & { onDeleted?: () => void }) {
  const route = useRoute()
  const container = useRef<HTMLDivElement>(null)
  const [layoutWidth, setLayoutWidth] = useState<number | undefined>(undefined)
  useLayoutEffect(() => {
    if (!embedded || !container.current) return
    const element = container.current
    const update = () => setLayoutWidth(element.getBoundingClientRect().width)
    update()
    const observer = new ResizeObserver(update)
    observer.observe(element)
    return () => observer.disconnect()
  }, [embedded])
  const view = useResultViewer({ archivePath, taskId: taskIdProp, startAt, embedded, layoutWidth, onTarget })
  const { prefs, update: updatePrefs } = usePreferences()
  const {
    mediaUrl,
    videoLoop,
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
    speakerSubtitles,
    speakerQuality,
    duration,
    currentTime,
    seekTo,
    handleRenameSpeaker,
    renamingSpeaker,
    hasUnsavedTranscript,
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
    showDeleteDialog,
    resolvedTaskId,
    mergeInfo,
    resolveMerge,
    derivedContentStale,
    speakerHints,
    speakerAutoFill,
    autoFilling,
    handleAutoFillSpeakers,
    regenerating,
    handleRegenerateDerived,
  } = view
  const [pendingNavigation, setPendingNavigation] = useState<(() => void) | null>(null)
  const navigationBusy = renamingSpeaker || Boolean(mergeInfo)
  const navigationDirty = hasUnsavedTranscript || editingTitle
  const allowNavigation = useRef(false)
  useEffect(() => setNavigationGuard((proceed, hash) => {
    if (allowNavigation.current || (!navigationDirty && !navigationBusy)) return true
    if (embedded && hash) {
      const current = parseHash(libraryHash())
      const target = parseHash(hash)
      if (current.page === "files" && target.page === "files" && ["view", "item", "attempt", "viewerTask", "generation"].every((key) => current.params[key] === target.params[key])) return true
    }
    setPendingNavigation(() => proceed)
    return false
  }), [navigationDirty, navigationBusy, embedded])
  useEffect(() => {
    const unload = (event: BeforeUnloadEvent) => {
      if (navigationDirty || navigationBusy) { event.preventDefault(); event.returnValue = "" }
    }
    window.addEventListener("beforeunload", unload)
    return () => window.removeEventListener("beforeunload", unload)
  }, [navigationDirty, navigationBusy])
  const [confirmRerun, setConfirmRerun] = useState(false)
  // Renaming from the ⋯ menu: keep focus in the title field instead of returning it to the menu button.
  const renameFromMenu = useRef(false)
  const [errorExpanded, setErrorExpanded] = useState(false)
  const resumeAction = taskStatus === "paused"
    ? { label: "继续处理", hint: "从暂停的地方继续" }
    : checkpointAction(taskStatus)
  const canFullRerun = Boolean(resolvedTaskId) && !isProcessing
  const taskFailed = taskStatus === "failed"
  const taskStalled = taskFailed || taskStatus === "paused" || taskStatus === "cancelled"
  const failedStepLabel = taskFailed
    ? taskFlow?.steps?.find((step) => step.id === taskFlow.current_step)?.label
    : undefined
  const visibleStatusEvents = taskFailed && taskError
    ? latestStatusEvents.filter((event) => event.level !== "error")
    : latestStatusEvents

  const phoneAvLayout = isMobileLayout && !isNoteContent
  const mediaPlayerBlock = mediaUrl ? (
    <div className={cn("sticky top-0 z-10 bg-background", !phoneAvLayout && "pb-2")}>
      <MediaPlayer
        src={mediaUrl}
        type={mediaType}
        bindMedia={bindMedia}
        subtitleSrt={transcript ?? undefined}
        loop={videoLoop}
        onLoopChange={setVideoLoop}
        mobile={phoneAvLayout}
        poster={archive?.thumbnail_url ?? (archive?.has_thumbnail ? api.archives.thumbnailUrl(archivePath) : undefined)}
        title={displayTitle ?? undefined}
        autoPlay={route.params.play === "1"}
      />
    </div>
  ) : archive?.media_file && !online ? (
    <div className="flex min-h-32 items-center justify-center rounded-lg border bg-muted/20 px-4 text-center text-sm text-muted-foreground">
      连接服务器后播放音视频；摘要、字幕、导图和图文资料可继续离线阅读。
    </div>
  ) : null

  const titleArea = editingTitle ? (
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
        <HugeiconsIcon icon={PencilEdit01Icon} className="h-3.5 w-3.5 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100" />
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
        <AttemptsMenu source={sourceHref} currentPath={archivePath} onTarget={onTarget} />
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
        {transcript && (
          <span
            className={cn(
              "rounded px-1.5 py-0.5 text-[10px] font-medium",
              subtitleSourceType === "platform"
                ? "text-foreground"
                : "text-muted-foreground",
            )}
            title={subtitleSourceType === "platform" ? "字幕来自平台" : subtitleSourceType === "asr" ? "字幕由 ASR 生成" : "字幕来源未记录"}
          >
            {subtitleSourceType === "platform" ? "平台" : subtitleSourceType === "asr" ? "ASR" : "来源未记录"}
          </span>
        )}
        {isPolished && (
          <span className="rounded p-1 text-primary" title="已润色">
            <HugeiconsIcon icon={PencilEdit01Icon} className="h-3.5 w-3.5" strokeWidth={1.75} />
          </span>
        )}
      </div>
    </div>
  )
  const processingBadge = isProcessing && (
    <span className="text-xs text-foreground flex items-center gap-1">
      <HugeiconsIcon icon={Loading03Icon} className="h-3 w-3 animate-spin" />
      处理中
    </span>
  )
  const moreMenu = (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button className="h-11 w-11 md:h-8 md:w-8" variant="ghost" size="icon-sm" aria-label="更多操作" title="更多操作">
          <HugeiconsIcon icon={MoreHorizontalIcon} className="h-4 w-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        onCloseAutoFocus={(event) => {
          if (renameFromMenu.current) {
            renameFromMenu.current = false
            event.preventDefault()
          }
        }}
      >
        {capabilities.archive_mutation && !editingTitle && (
          <DropdownMenuItem onClick={() => { renameFromMenu.current = true; startEditTitle() }}>
            <HugeiconsIcon icon={PencilEdit01Icon} className="h-4 w-4" />
            重命名
          </DropdownMenuItem>
        )}
        {capabilities.archive_mutation && !editingTitle && (resumeAction || canFullRerun) && <DropdownMenuSeparator />}
        {resumeAction && (
          <DropdownMenuItem disabled={resuming} onClick={handleResumeFromCheckpoint} title={resumeAction.hint}>
            <HugeiconsIcon
              icon={resuming ? Loading03Icon : taskStatus === "paused" ? PlayIcon : taskStatus === "completed" ? AiMagicIcon : PlayCircleIcon}
              className={resuming ? "h-4 w-4 animate-spin" : "h-4 w-4"}
            />
            {resuming ? "正在加入队列" : resumeAction.label}
          </DropdownMenuItem>
        )}
        {canFullRerun && (
          <DropdownMenuItem disabled={rerunning} onClick={() => setConfirmRerun(true)} title="删除已生成的字幕、说话人、摘要和导图后从头处理">
            <HugeiconsIcon icon={rerunning ? Loading03Icon : RefreshIcon} className={rerunning ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
            {rerunning ? "正在加入队列" : "全部重新处理…"}
          </DropdownMenuItem>
        )}
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
  )

  const speakerTools = {
    hints: speakerHints,
    onAutoFill: resolvedTaskId && online && capabilities.archive_mutation && !isProcessing ? () => void handleAutoFillSpeakers() : undefined,
    autoFill: {
      busy: autoFilling || (isProcessing && ["queued", "running"].includes(speakerAutoFill?.status ?? "")),
      error: speakerAutoFill?.status === "failed" ? speakerAutoFill.error ?? "未知原因" : null,
    },
    // The summary and mind map tabs say it themselves while they are open.
    stale: derivedContentStale && !isProcessing && !["summary", "mindmap"].includes(view.activeTab),
    onRegenerate: resolvedTaskId && online ? () => void handleRegenerateDerived() : undefined,
    regenerating,
  }

  const mediaPane = (
    <div className={cn(
      "h-full min-h-0 overflow-y-auto",
      isPortraitLayout || isMobileLayout ? "p-0" : "p-4",
    )}>
      {embedded && <div className="mb-3 flex min-w-0 items-center gap-2">{titleArea}{processingBadge}{moreMenu}</div>}
      {embedded && taskError && <p role="alert" className="mb-3 text-sm text-destructive">{taskError}</p>}
      <div className={cn(
        "space-y-3",
        // Video results: keep the player near eye level rather than at the top edge.
        !embedded && !isNoteContent && !isPortraitLayout && !isMobileLayout && "flex min-h-full flex-col justify-center",
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
          subtitles={speakerSubtitles}
          quality={speakerQuality}
          duration={duration}
          currentTime={currentTime}
          onSeek={seekTo}
          onRenameSpeaker={handleRenameSpeaker}
          renaming={renamingSpeaker}
          editingDisabled={hasUnsavedTranscript || Boolean(mergeInfo)}
          collapsed={prefs.speakerPanelCollapsed}
          onCollapsedChange={(value) => updatePrefs({ speakerPanelCollapsed: value })}
          {...speakerTools}
        />
      )}
      </div>
    </div>
  )
  const contentPane = <ResultContentPane view={view}
    mobileOverview={phoneAvLayout ? <MobileMediaOverview view={view} actions={moreMenu} /> : undefined}
    mobileSpeakers={phoneAvLayout && subtitles.length > 0 ? <SpeakerPanel compact subtitles={speakerSubtitles} quality={speakerQuality} duration={duration} currentTime={currentTime} onSeek={seekTo} onRenameSpeaker={handleRenameSpeaker} renaming={renamingSpeaker} editingDisabled={hasUnsavedTranscript || Boolean(mergeInfo)} {...speakerTools} /> : undefined}
  />


  return (
    <div ref={container} className={cn("flex h-full min-w-0 flex-col", isMobileLayout && isNoteContent && "pt-12")}>
      {/* Title bar */}
      {!embedded && !phoneAvLayout && <div className="flex shrink-0 items-center gap-2 border-b px-2 py-1.5 sm:gap-3 sm:px-4 sm:py-2">
        <Button className="h-11 md:h-8" variant="ghost" size="sm" onClick={() => navigate(libraryHash())}>
          <HugeiconsIcon icon={ArrowLeft01Icon} className="h-4 w-4 mr-1" />
          返回
        </Button>
        {titleArea}
        {processingBadge}
        {moreMenu}
      </div>}

      {!embedded && !phoneAvLayout && showFlowDiagnostics && (taskFlow || recentTimelineEvents.length > 0) && (
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
              <Progress
                value={flowProgress}
                className={cn("mt-2 h-1.5", taskFailed && "[&_[data-slot=progress-indicator]]:bg-destructive")}
              />
            </>
          )}
          {taskFlow?.steps?.length ? (
            <div className="mt-3 flex flex-wrap items-center gap-1.5">
              {taskFlow.steps.map((step) => {
                const isDone = flowCompletedSteps.includes(step.id)
                const isCurrent = taskFlow.current_step === step.id
                const isFailedStep = isCurrent && !isDone && taskFailed
                const isStalledStep = isCurrent && !isDone && taskStalled && !taskFailed
                return (
                  <span
                    key={step.id}
                    className={cn(
                      "inline-flex h-7 items-center gap-1.5 rounded-md border px-2 text-xs transition-colors",
                      isDone && "border-foreground/20 bg-muted text-foreground",
                      isFailedStep && "border-destructive bg-destructive/10 font-medium text-destructive",
                      isStalledStep && "border-foreground/40 bg-muted text-foreground",
                      isCurrent && !isDone && !taskStalled && "border-foreground bg-foreground text-background",
                      !isDone && !isCurrent && "border-border bg-muted/30 text-muted-foreground",
                    )}
                  >
                    {isDone ? (
                      <HugeiconsIcon icon={Tick02Icon} className="h-3 w-3" />
                    ) : isFailedStep ? (
                      <HugeiconsIcon icon={Cancel01Icon} className="h-3 w-3" />
                    ) : isStalledStep ? (
                      <HugeiconsIcon icon={PauseIcon} className="h-3 w-3" />
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
          {visibleStatusEvents.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {visibleStatusEvents.map((event) => (
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
      {!embedded && !phoneAvLayout && taskFailed && taskError && (
        <div role="alert" className="mx-4 mt-2 shrink-0 space-y-2 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm">
          <p className="font-medium text-destructive">处理失败{failedStepLabel ? `：${failedStepLabel}` : ""}</p>
          <p className={cn("whitespace-pre-wrap break-words text-destructive/90", !errorExpanded && "line-clamp-3")}>{taskError}</p>
          <div className="flex flex-wrap items-center gap-2">
            {resumeAction && (
              <Button size="sm" onClick={handleResumeFromCheckpoint} disabled={resuming} title={resumeAction.hint}>
                <HugeiconsIcon icon={resuming ? Loading03Icon : PlayCircleIcon} className={resuming ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
                {resumeAction.label}
              </Button>
            )}
            {resolvedTaskId && (
              <Button
                size="sm"
                variant="outline"
                onClick={() => navigate(buildHash("backend", { tab: "logs", q: resolvedTaskId.slice(0, 8) }))}
              >
                查看日志
              </Button>
            )}
            {taskError.length > 160 && (
              <Button size="sm" variant="ghost" onClick={() => setErrorExpanded((value) => !value)}>
                {errorExpanded ? "收起" : "展开全部"}
              </Button>
            )}
          </div>
        </div>
      )}

      {/* Main content area — three-column layout */}
      <div className="flex-1 min-h-0 relative">
        {phoneAvLayout ? (
          <div className="absolute inset-0 flex flex-col">
            <div className="shrink-0 bg-background" data-mobile-player>
              {mediaPlayerBlock ?? (isProcessing ? (
                <div className="flex h-24 items-center justify-center rounded-lg bg-muted/50 text-xs text-muted-foreground">
                  <HugeiconsIcon icon={Loading03Icon} className="mr-2 h-4 w-4 animate-spin" />
                  正在下载媒体…
                </div>
              ) : null)}
            </div>
            <div className="min-h-0 flex-1">
              {contentPane}
            </div>
          </div>
        ) : isMobileLayout ? (
          <div className="absolute inset-0 overflow-y-auto p-3">
            <div className="space-y-4">
              <section aria-label="图文" className="h-[min(58dvh,36rem)] min-h-[22rem] overflow-hidden">
                {mediaPane}
              </section>
              <section aria-label="知识内容" className="h-[calc(100dvh-10rem)] min-h-[32rem] overflow-hidden">
                {contentPane}
              </section>
            </div>
          </div>
        ) : isPortraitLayout ? (
          <div className="absolute inset-0 overflow-hidden p-3">
            <div className={cn("grid h-full min-h-0 grid-rows-[minmax(220px,42%)_minmax(0,1fr)] gap-3", embedded ? layoutWidth !== undefined && layoutWidth >= 640 && "grid-cols-[minmax(260px,0.95fr)_minmax(300px,1fr)] grid-rows-1" : "sm:grid-cols-[minmax(260px,0.95fr)_minmax(300px,1fr)] sm:grid-rows-1")}>
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
          {/* Left panel — media, speakers */}
          <ResizablePanel defaultSize="50%" minSize="20%" maxSize="70%">
            {mediaPane}
          </ResizablePanel>

          <ResizableHandle withHandle />

          {/* Right panel — tabbed content */}
          <ResizablePanel defaultSize="50%" minSize="25%">
            {contentPane}
          </ResizablePanel>
        </ResizablePanelGroup>
        )}
      </div>

      <Dialog open={pendingNavigation !== null} onOpenChange={(open) => { if (!open) setPendingNavigation(null) }}>
        <DialogContent><DialogHeader><DialogTitle>{navigationBusy ? "请等待当前操作完成" : "有未保存的编辑"}</DialogTitle><DialogDescription>{navigationBusy ? "人物名称正在保存，或合并尚未确认。完成后再切换。" : "切换后会放弃当前编辑。也可以返回字幕，保存后再切换。"}</DialogDescription></DialogHeader><DialogFooter><Button variant="ghost" onClick={() => setPendingNavigation(null)}>继续编辑</Button>{!navigationBusy && <Button variant="destructive" onClick={() => { allowNavigation.current = true; const proceed = pendingNavigation; setPendingNavigation(null); proceed?.(); queueMicrotask(() => { allowNavigation.current = false }) }}>放弃并切换</Button>}</DialogFooter></DialogContent>
      </Dialog>
      {/* Delete confirmation dialog */}
      <DeleteConfirmDialog
        open={showDeleteDialog}
        onOpenChange={setShowDeleteDialog}
        title={displayTitle ?? ""}
        archivePath={archivePath}
        taskId={resolvedTaskId ?? undefined}
        taskDelete={Boolean(isProcessing && resolvedTaskId)}
        onDeleted={onDeleted ?? (() => navigate(libraryHash()))}
      />

      <RerunConfirmDialog
        open={confirmRerun}
        onOpenChange={setConfirmRerun}
        title={displayTitle ?? ""}
        onConfirm={() => {
          setConfirmRerun(false)
          void handleFullRerun()
        }}
      />

      {/* Speaker merge confirmation */}
      <SpeakerMergeDialog info={mergeInfo} onResolve={resolveMerge} />
    </div>
  )
}
