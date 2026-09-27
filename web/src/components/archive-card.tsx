import { useState, type MouseEvent as ReactMouseEvent } from "react"
import type { ArchiveItem } from "@/hooks/use-archives"
import { formatDuration } from "@/lib/format"
import { api, type Task } from "@/lib/api"
import { STEP_NAME } from "@/lib/constants"
import { cn } from "@/lib/utils"
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from "@/components/ui/context-menu"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { RenameDialog } from "@/components/rename-dialog"
import { PlatformIcon } from "@/components/platform-icon"
import { HugeiconsIcon } from "@hugeicons/react"
import {
  Video01Icon, MusicNote01Icon, Note01Icon, Image01Icon, FolderOpenIcon, PencilEdit01Icon, Delete01Icon,
  Loading03Icon, RefreshIcon, PauseIcon, PlayIcon, PlayCircleIcon, AiMagicIcon, FileVideoIcon,
  MoreHorizontalIcon, Tick02Icon,
} from "@hugeicons/core-free-icons"
import { archiveDisplayTitle, checkpointAction } from "@/lib/task-display"
import { normalizeSourceFilter } from "@/lib/archive-filters"

interface ArchiveCardProps {
  archive: ArchiveItem
  onClick: () => void
  onDelete?: () => void
  onMediaRetention?: () => void
  onRenamed?: (newTitle: string) => void
  onRerun?: () => void
  onCheckpointRerun?: () => void
  onPause?: () => void
  onResume?: () => void
  rerunning?: boolean
  checkpointRerunning?: boolean
  taskActionBusy?: boolean
  compact?: boolean
  /** Live task state (progress, current step) while the archive is being processed. */
  task?: Task
  /** In selection mode a click toggles selection instead of opening the archive. */
  selectionMode?: boolean
  selected?: boolean
  onSelect?: (event: ReactMouseEvent) => void
}

type MenuKind = "context" | "dropdown"

export function ArchiveCard({
  archive,
  onClick,
  onDelete,
  onMediaRetention,
  onRenamed,
  onRerun,
  onCheckpointRerun,
  onPause,
  onResume,
  rerunning = false,
  checkpointRerunning = false,
  taskActionBusy = false,
  compact = false,
  task,
  selectionMode = false,
  selected = false,
  onSelect,
}: ArchiveCardProps) {
  const [imgError, setImgError] = useState(false)
  const [showRename, setShowRename] = useState(false)
  const contentSubtype = typeof archive.metadata?.content_subtype === "string"
    ? archive.metadata.content_subtype
    : null
  const isImageNote = archive.has_image || contentSubtype === "image_note"
  const isTextNote = contentSubtype === "text_note" || (archive.metadata.media_removed === true && !archive.has_image)
  const mediaLabel = archive.has_video ? "视频" : isImageNote ? "图文" : isTextNote ? "正文" : "音频"
  const metadataStatus = typeof archive.metadata?.status === "string" ? archive.metadata.status : null
  const errorDetail = typeof archive.metadata?.error === "string" ? archive.metadata.error : "任务处理失败，点开查看详情"
  const status = archive.processing
    ? (metadataStatus === "paused" ? "paused" : "processing")
    : (metadataStatus ?? "completed")
  const failed = status === "failed"
  const cancelled = status === "cancelled"
  const canPause = status === "processing" && Boolean(onPause)
  const canResume = status === "paused" && Boolean(onResume)
  const checkpoint = checkpointAction(status)
  const showCheckpoint = Boolean(checkpoint && onCheckpointRerun)
  const showFullRerun = Boolean(onRerun) && !archive.processing
  const { title, untitled } = archiveDisplayTitle(archive)
  const uploader = typeof archive.metadata?.uploader === "string" ? archive.metadata.uploader : null
  const progress = task ? Math.round((task.progress ?? 0) * 100) : null
  // Only unusual gaps are worth a word; nearly every finished video has both.
  const exceptions = status === "completed" && !isImageNote && !isTextNote
    ? [!archive.has_transcript && "无字幕", !archive.has_summary && "无摘要"].filter((label): label is string => Boolean(label))
    : []
  const stepLabel = task?.current_step ? (STEP_NAME[task.current_step] ?? task.current_step) : null

  const showThumbnail = archive.has_thumbnail !== false
    && !imgError
    && (!isTextNote || archive.has_image)
  const thumbnailUrl = archive.thumbnail_url ?? api.archives.thumbnailUrl(archive.path)
  const thumbnailClassName =
    "h-full w-full object-cover object-center transition-opacity duration-150 group-hover:opacity-90"

  // One quick action on hover, matching what this card most likely needs next.
  const quickAction = failed || cancelled
    ? (showCheckpoint ? { label: "重试", run: onCheckpointRerun, busy: checkpointRerunning } : null)
    : status === "paused"
      ? (canResume ? { label: "继续", run: onResume, busy: taskActionBusy } : null)
      : null

  const statusPill = status === "processing"
    ? { text: task?.status === "queued" ? "排队中" : `${stepLabel ?? "处理中"}${progress !== null ? ` ${progress}%` : ""}`, tone: "running" }
    : status === "paused"
      ? { text: `已暂停${progress !== null ? ` · ${progress}%` : ""}`, tone: "paused" }
      : failed
        ? { text: "失败", tone: "failed" }
        : cancelled
          ? { text: "已取消", tone: "muted" }
          : null

  const renderActions = (kind: MenuKind) => {
    const Item = kind === "context" ? ContextMenuItem : DropdownMenuItem
    const Separator = kind === "context" ? ContextMenuSeparator : DropdownMenuSeparator
    return (
      <>
        <Item onClick={onClick}>
          <HugeiconsIcon icon={FolderOpenIcon} className="h-4 w-4" />
          打开
        </Item>
        {(canPause || canResume || showCheckpoint) && <Separator />}
        {canPause && (
          <Item disabled={taskActionBusy} onClick={() => onPause?.()}>
            <HugeiconsIcon icon={taskActionBusy ? Loading03Icon : PauseIcon} className={taskActionBusy ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
            暂停
          </Item>
        )}
        {canResume && (
          <Item disabled={taskActionBusy} onClick={() => onResume?.()}>
            <HugeiconsIcon icon={taskActionBusy ? Loading03Icon : PlayIcon} className={taskActionBusy ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
            继续处理
          </Item>
        )}
        {showCheckpoint && checkpoint && (
          <Item disabled={checkpointRerunning} onClick={() => onCheckpointRerun?.()} title={checkpoint.hint}>
            <HugeiconsIcon
              icon={checkpointRerunning ? Loading03Icon : status === "completed" ? AiMagicIcon : PlayCircleIcon}
              className={checkpointRerunning ? "h-4 w-4 animate-spin" : "h-4 w-4"}
            />
            {checkpointRerunning ? "正在加入队列" : checkpoint.label}
          </Item>
        )}
        {(onRenamed || onMediaRetention) && <Separator />}
        {onRenamed && (
          <Item onClick={() => setShowRename(true)}>
            <HugeiconsIcon icon={PencilEdit01Icon} className="h-4 w-4" />
            重命名
          </Item>
        )}
        {onMediaRetention && (
          <Item onClick={onMediaRetention}>
            <HugeiconsIcon icon={FileVideoIcon} className="h-4 w-4" />
            媒体保留…
          </Item>
        )}
        {(showFullRerun || onDelete) && <Separator />}
        {showFullRerun && (
          <Item disabled={rerunning} onClick={() => onRerun?.()} title="删除已生成的字幕、说话人、摘要和导图后从头处理">
            <HugeiconsIcon icon={rerunning ? Loading03Icon : RefreshIcon} className={rerunning ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
            {rerunning ? "正在加入队列" : "全部重新处理…"}
          </Item>
        )}
        {onDelete && (
          <Item variant="destructive" onClick={onDelete}>
            <HugeiconsIcon icon={Delete01Icon} className="h-4 w-4" />
            删除
          </Item>
        )}
      </>
    )
  }

  return (
    <>
    <ContextMenu>
      <ContextMenuTrigger>
        <div className="group relative" data-selected={selected || undefined}>
          <button
            type="button"
            onClick={(event) => (selectionMode && onSelect ? onSelect(event) : onClick())}
            data-archive-path={archive.path}
            aria-pressed={selectionMode ? selected : undefined}
            className="flex w-full flex-col rounded-lg text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {/* Thumbnail */}
            <div className={cn(
              compact ? "relative aspect-[535/304] w-full overflow-hidden rounded-lg bg-muted" : "relative aspect-video w-full overflow-hidden rounded-lg bg-muted",
              selected && "ring-2 ring-primary ring-offset-2 ring-offset-background",
            )}>
              {showThumbnail && thumbnailUrl ? (
                <img
                  src={thumbnailUrl}
                  alt=""
                  loading="lazy"
                  decoding="async"
                  onError={() => setImgError(true)}
                  className={thumbnailClassName}
                />
              ) : archive.has_video ? (
                <div className="flex h-full w-full items-center justify-center">
                  <HugeiconsIcon icon={Video01Icon} className="h-8 w-8 text-muted-foreground/30" />
                </div>
              ) : (
                <div className="flex h-full w-full items-center justify-center bg-muted">
                  <div className="p-3">
                    <HugeiconsIcon
                      icon={isImageNote ? Image01Icon : isTextNote ? Note01Icon : MusicNote01Icon}
                      className="h-6 w-6 text-muted-foreground/50"
                    />
                  </div>
                </div>
              )}

              {/* Task state sits in the corner so the cover itself stays clean */}
              {statusPill && (
                <span
                  className={cn(
                    "absolute bottom-1.5 left-1.5 inline-flex max-w-[70%] items-center gap-1 truncate rounded px-1.5 py-0.5 text-[11px] font-medium text-white tabular-nums",
                    statusPill.tone === "failed" ? "bg-red-600" : "bg-black/70",
                  )}
                  title={failed ? errorDetail : statusPill.text}
                >
                  {statusPill.tone === "running" && <span className="size-1.5 shrink-0 animate-pulse rounded-full bg-primary" />}
                  {statusPill.tone === "paused" && <span className="size-1.5 shrink-0 rounded-full bg-amber-400" />}
                  <span className="truncate">{statusPill.text}</span>
                </span>
              )}

              {/* Duration badge */}
              {archive.duration_seconds != null && archive.duration_seconds > 0 && (
                <span className="absolute bottom-1.5 right-1.5 rounded bg-black/70 px-1.5 py-0.5 text-[11px] font-medium text-white tabular-nums">
                  {formatDuration(archive.duration_seconds)}
                </span>
              )}

              {progress !== null && (status === "processing" || status === "paused") && (
                <span className="absolute inset-x-0 bottom-0 h-[3px] bg-white/30" aria-hidden="true">
                  <span
                    className={cn("block h-full", status === "paused" ? "bg-amber-400" : "bg-primary")}
                    style={{ width: `${progress}%` }}
                  />
                </span>
              )}
            </div>

            {/* Info */}
            <div data-archive-info className={compact ? "flex flex-col gap-0 pt-[3px] px-0.5" : "flex flex-col gap-1 pt-2 px-0.5"}>
              <h3
                className={compact ? "line-clamp-2 min-h-[2lh] text-[13px] font-medium leading-[1.2] group-hover:text-primary transition-colors" : "line-clamp-2 min-h-[2lh] text-sm font-medium leading-snug group-hover:text-primary transition-colors"}
                title={untitled ? `${title}（没有取到标题）` : title}
              >
                {title}
                {untitled && <span className="ml-1 text-xs font-normal text-muted-foreground">未取得标题</span>}
              </h3>
              {/* Fixed height: the page size is computed from one card, so every card must be as tall */}
              <div className={compact ? "flex h-3.5 min-w-0 items-center gap-1.5 overflow-hidden text-[11px] leading-none text-muted-foreground" : "flex h-4 min-w-0 items-center gap-2 overflow-hidden text-xs leading-none text-muted-foreground"}>
                <span className="shrink-0 tabular-nums">{formatArchiveTime(archive.created_at, archive.date)}</span>
                <span className="flex shrink-0 items-center" title={mediaLabel}>
                  <HugeiconsIcon
                    icon={archive.has_video ? Video01Icon : isImageNote ? Image01Icon : isTextNote ? Note01Icon : MusicNote01Icon}
                    className="h-3.5 w-3.5"
                    strokeWidth={1.75}
                  />
                </span>
                {typeof archive.metadata?.platform === "string" && (
                  normalizeSourceFilter(archive.metadata.platform) === "local" ? (
                    <span className="flex shrink-0 items-center" title="本地文件">
                      <HugeiconsIcon icon={FolderOpenIcon} className="h-3.5 w-3.5" strokeWidth={1.75} />
                    </span>
                  ) : (
                    <PlatformIcon
                      platform={archive.metadata.platform as string}
                      uploader={uploader}
                      className="h-3.5 w-3.5 shrink-0"
                    />
                  )
                )}
                {uploader && <span className="min-w-0 truncate" title={uploader}>{uploader}</span>}
                {exceptions.map((label) => (
                  <span key={label} className="shrink-0 text-amber-700 dark:text-amber-400">{label}</span>
                ))}
                {archive.attempts && archive.attempts > 1 && (
                  <span
                    className="ml-auto shrink-0 rounded bg-muted px-1 py-px text-[10px] leading-none tabular-nums text-foreground/80"
                    title={`同一个来源处理过 ${archive.attempts} 次，这里显示最好的一次`}
                  >
                    ×{archive.attempts}
                  </span>
                )}
              </div>
            </div>
          </button>

          {/* Selection checkbox: always shown in selection mode, on hover otherwise */}
          {onSelect && (
            <button
              type="button"
              role="checkbox"
              aria-checked={selected}
              aria-label={`选择「${title}」`}
              onClick={(event) => { event.stopPropagation(); onSelect(event) }}
              className={cn(
                "absolute left-1.5 top-1.5 z-10 flex size-5 items-center justify-center rounded border-[1.5px] shadow-sm transition-opacity",
                selected ? "border-primary bg-primary text-primary-foreground" : "border-white bg-black/30 text-transparent",
                selectionMode || selected ? "opacity-100" : "opacity-0 focus-visible:opacity-100 group-hover:opacity-100",
              )}
            >
              <HugeiconsIcon icon={Tick02Icon} className="size-3.5" strokeWidth={2.5} />
            </button>
          )}

          {/* Hover actions: one quick action + the same menu as right-click */}
          {!selectionMode && (
            <div className="absolute right-1.5 top-1.5 z-10 flex items-center gap-1 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
              {quickAction && (
                <button
                  type="button"
                  disabled={quickAction.busy}
                  onClick={(event) => { event.stopPropagation(); quickAction.run?.() }}
                  className="h-6 rounded bg-background/95 px-2 text-xs font-medium shadow-sm hover:bg-background disabled:opacity-60"
                >
                  {quickAction.busy ? "…" : quickAction.label}
                </button>
              )}
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <button
                    type="button"
                    aria-label={`「${title}」的更多操作`}
                    onClick={(event) => event.stopPropagation()}
                    className="flex size-6 items-center justify-center rounded bg-background/95 shadow-sm hover:bg-background"
                  >
                    <HugeiconsIcon icon={MoreHorizontalIcon} className="size-4" />
                  </button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  {renderActions("dropdown")}
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          )}
        </div>
      </ContextMenuTrigger>

      <ContextMenuContent>
        {renderActions("context")}
      </ContextMenuContent>
    </ContextMenu>

    {onRenamed && <RenameDialog
      open={showRename}
      onOpenChange={setShowRename}
      archivePath={archive.path}
      currentTitle={archive.title}
      onRenamed={(newTitle) => onRenamed?.(newTitle)}
    />}
    </>
  )
}

function formatArchiveTime(createdAt: string | undefined, fallbackDate: string): string {
  if (!createdAt) return fallbackDate
  const date = new Date(createdAt)
  if (Number.isNaN(date.getTime())) return fallbackDate
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, "0")
  const day = String(date.getDate()).padStart(2, "0")
  const hour = String(date.getHours()).padStart(2, "0")
  const minute = String(date.getMinutes()).padStart(2, "0")
  return `${year}-${month}-${day} ${hour}:${minute}`
}
