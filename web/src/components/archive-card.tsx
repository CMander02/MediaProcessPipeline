import { useState } from "react"
import type { ArchiveItem } from "@/hooks/use-archives"
import { formatDuration } from "@/lib/format"
import { api } from "@/lib/api"
import { cn } from "@/lib/utils"
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from "@/components/ui/context-menu"
import { RenameDialog } from "@/components/rename-dialog"
import { PlatformIcon } from "@/components/platform-icon"
import { HugeiconsIcon } from "@hugeicons/react"
import { Video01Icon, MusicNote01Icon, Note01Icon, Image01Icon, ListTreeIcon, FolderOpenIcon, PencilEdit01Icon, Delete01Icon, Loading03Icon, RefreshIcon, PauseIcon, PlayIcon, PlayCircleIcon, AiMagicIcon, FileVideoIcon } from "@hugeicons/core-free-icons"
import { checkpointAction } from "@/lib/task-display"

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
}

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
  const failed = metadataStatus === "failed"
  const errorDetail = typeof archive.metadata?.error === "string" ? archive.metadata.error : "任务处理失败，点击查看详情"
  const errorSummary = errorDetail.replace(/\s+/g, " ").split(/[：:\n]/)[0].slice(0, 50) || "任务处理失败"
  const canPause = archive.processing && metadataStatus !== "paused" && Boolean(onPause)
  const canResume = archive.processing && metadataStatus === "paused" && Boolean(onResume)
  const status = archive.processing
    ? (metadataStatus === "paused" ? "paused" : "processing")
    : (metadataStatus ?? "completed")
  const checkpoint = checkpointAction(status)
  const showCheckpoint = Boolean(checkpoint && onCheckpointRerun)
  const showFullRerun = Boolean(onRerun) && !archive.processing

  const showThumbnail = archive.has_thumbnail !== false
    && !imgError
    && (!isTextNote || archive.has_image)
  const thumbnailUrl = archive.thumbnail_url ?? api.archives.thumbnailUrl(archive.path)
  const thumbnailClassName =
    "h-full w-full object-cover object-center transition-opacity duration-150 group-hover:opacity-90"

  return (
    <>
    <ContextMenu>
      <ContextMenuTrigger>
        <button
          onClick={onClick}
          data-archive-path={archive.path}
          className="group flex flex-col text-left w-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-lg"
        >
          {/* Thumbnail */}
          <div className={compact ? "relative aspect-[535/304] w-full overflow-hidden rounded-lg bg-muted" : "relative aspect-video w-full overflow-hidden rounded-lg bg-muted"}>
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

            {/* Processing indicator */}
            {archive.processing && (
              <div className="absolute inset-0 flex items-center justify-center bg-black/20">
                <div className="rounded-md bg-background/90 p-1.5">
                  <HugeiconsIcon
                    icon={metadataStatus === "paused" ? PauseIcon : Loading03Icon}
                    className={cn(
                      "h-5 w-5",
                      metadataStatus === "paused" ? "text-muted-foreground" : "animate-spin text-foreground",
                    )}
                  />
                </div>
              </div>
            )}

            {failed && (
              <div className="absolute inset-0 rounded-lg bg-red-950/20 ring-2 ring-inset ring-red-500/70">
                <span className="absolute left-2 top-2 rounded bg-red-600 px-2 py-0.5 text-xs font-medium text-white shadow-sm">
                  ! 处理失败
                </span>
              </div>
            )}

            {/* Duration badge */}
            {archive.duration_seconds != null && archive.duration_seconds > 0 && (
              <span className="absolute bottom-1.5 right-1.5 rounded bg-black/70 px-1.5 py-0.5 text-[11px] font-medium text-white tabular-nums">
                {formatDuration(archive.duration_seconds)}
              </span>
            )}
          </div>

          {/* Info */}
          <div data-archive-info className={compact ? "flex flex-col gap-0 pt-[3px] px-0.5" : "flex flex-col gap-1 pt-2 px-0.5"}>
            <h3 className={compact ? "line-clamp-2 min-h-[2lh] text-[13px] font-medium leading-[1.2] group-hover:text-primary transition-colors" : "line-clamp-2 min-h-[2lh] text-sm font-medium leading-snug group-hover:text-primary transition-colors"}>
              {archive.title}
            </h3>
            <div className={compact ? "flex items-center gap-1.5 text-[11px] leading-tight text-muted-foreground [&>span.rounded]:py-0" : "flex items-center gap-2 text-xs text-muted-foreground"}>
              {failed ? (
                <span className="min-w-0 flex-1 truncate font-medium text-red-600 dark:text-red-400" title={errorDetail}>
                  {errorSummary}
                </span>
              ) : <span>{formatArchiveTime(archive.created_at, archive.date)}</span>}
              <span className="flex items-center" title={mediaLabel}>
                {archive.has_video ? (
                  <HugeiconsIcon icon={Video01Icon} className="h-3.5 w-3.5" strokeWidth={1.75} />
                ) : isImageNote ? (
                  <HugeiconsIcon icon={Image01Icon} className="h-3.5 w-3.5" strokeWidth={1.75} />
                ) : isTextNote ? (
                  <HugeiconsIcon icon={Note01Icon} className="h-3.5 w-3.5" strokeWidth={1.75} />
                ) : (
                  <HugeiconsIcon icon={MusicNote01Icon} className="h-3.5 w-3.5" strokeWidth={1.75} />
                )}
              </span>
              {archive.has_summary && (
                <span className="flex items-center" title="摘要">
                  <HugeiconsIcon icon={Note01Icon} className="h-3.5 w-3.5" strokeWidth={1.75} />
                </span>
              )}
              {archive.has_mindmap && (
                <span className="flex items-center" title="总结树">
                  <HugeiconsIcon icon={ListTreeIcon} className="h-3.5 w-3.5" strokeWidth={1.75} />
                </span>
              )}
              {typeof archive.metadata?.platform === "string" && (
                <PlatformIcon
                  platform={archive.metadata.platform as string}
                  uploader={typeof archive.metadata?.uploader === "string" ? (archive.metadata.uploader as string) : null}
                  className="h-3.5 w-3.5 shrink-0"
                />
              )}
            </div>
          </div>
        </button>
      </ContextMenuTrigger>

      <ContextMenuContent>
        <ContextMenuItem onClick={onClick}>
          <HugeiconsIcon icon={FolderOpenIcon} className="h-4 w-4" />
          打开
        </ContextMenuItem>
        {(canPause || canResume || showCheckpoint) && <ContextMenuSeparator />}
        {canPause && (
          <ContextMenuItem disabled={taskActionBusy} onClick={() => onPause?.()}>
            <HugeiconsIcon icon={taskActionBusy ? Loading03Icon : PauseIcon} className={taskActionBusy ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
            暂停
          </ContextMenuItem>
        )}
        {canResume && (
          <ContextMenuItem disabled={taskActionBusy} onClick={() => onResume?.()}>
            <HugeiconsIcon icon={taskActionBusy ? Loading03Icon : PlayIcon} className={taskActionBusy ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
            继续处理
          </ContextMenuItem>
        )}
        {showCheckpoint && checkpoint && (
          <ContextMenuItem disabled={checkpointRerunning} onClick={() => onCheckpointRerun?.()} title={checkpoint.hint}>
            <HugeiconsIcon
              icon={checkpointRerunning ? Loading03Icon : status === "completed" ? AiMagicIcon : PlayCircleIcon}
              className={checkpointRerunning ? "h-4 w-4 animate-spin" : "h-4 w-4"}
            />
            {checkpointRerunning ? "正在加入队列" : checkpoint.label}
          </ContextMenuItem>
        )}
        {(onRenamed || onMediaRetention) && <ContextMenuSeparator />}
        {onRenamed && (
          <ContextMenuItem onClick={() => setShowRename(true)}>
            <HugeiconsIcon icon={PencilEdit01Icon} className="h-4 w-4" />
            重命名
          </ContextMenuItem>
        )}
        {onMediaRetention && (
          <ContextMenuItem onClick={onMediaRetention}>
            <HugeiconsIcon icon={FileVideoIcon} className="h-4 w-4" />
            媒体保留…
          </ContextMenuItem>
        )}
        {(showFullRerun || onDelete) && <ContextMenuSeparator />}
        {showFullRerun && (
          <ContextMenuItem disabled={rerunning} onClick={() => onRerun?.()} title="删除已生成的字幕、说话人、摘要和导图后从头处理">
            <HugeiconsIcon icon={rerunning ? Loading03Icon : RefreshIcon} className={rerunning ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
            {rerunning ? "正在加入队列" : "全部重新处理…"}
          </ContextMenuItem>
        )}
        {onDelete && (
          <ContextMenuItem variant="destructive" onClick={onDelete}>
            <HugeiconsIcon icon={Delete01Icon} className="h-4 w-4" />
            删除
          </ContextMenuItem>
        )}
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
