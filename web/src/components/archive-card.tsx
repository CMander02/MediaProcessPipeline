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
import { Video01Icon, MusicNote01Icon, Note01Icon, Image01Icon, ListTreeIcon, FolderOpenIcon, PencilEdit01Icon, Delete01Icon, Loading03Icon, RefreshIcon, PauseIcon, PlayIcon } from "@hugeicons/core-free-icons"

interface ArchiveCardProps {
  archive: ArchiveItem
  onClick: () => void
  onDelete?: () => void
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
  const isTextNote = contentSubtype === "text_note"
  const mediaLabel = archive.has_video ? "视频" : isImageNote ? "图文" : isTextNote ? "正文" : "音频"
  const metadataStatus = typeof archive.metadata?.status === "string" ? archive.metadata.status : null
  const canPause = archive.processing && metadataStatus !== "paused" && Boolean(onPause)
  const canResume = archive.processing && metadataStatus === "paused" && Boolean(onResume)

  const showThumbnail = !imgError && !isTextNote
  const thumbnailUrl = api.archives.thumbnailUrl(archive.path)
  const thumbnailClassName =
    "h-full w-full object-cover object-center transition-transform group-hover:scale-[1.03]"

  return (
    <>
    <ContextMenu>
      <ContextMenuTrigger>
        <button
          onClick={onClick}
          className="group flex flex-col text-left w-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-lg"
        >
          {/* Thumbnail */}
          <div className={compact ? "relative aspect-[16/8] w-full rounded-lg overflow-hidden bg-muted" : "relative aspect-video w-full rounded-lg overflow-hidden bg-muted"}>
            {showThumbnail && thumbnailUrl ? (
              <img
                src={thumbnailUrl}
                alt=""
                loading="lazy"
                onError={() => setImgError(true)}
                className={thumbnailClassName}
              />
            ) : archive.has_video ? (
              <div className="flex h-full w-full items-center justify-center">
                <HugeiconsIcon icon={Video01Icon} className="h-8 w-8 text-muted-foreground/30" />
              </div>
            ) : (
              <div className="flex h-full w-full items-center justify-center bg-gradient-to-br from-primary/5 to-primary/15">
                <div className="rounded-full bg-primary/10 p-3">
                  <HugeiconsIcon
                    icon={isImageNote ? Image01Icon : isTextNote ? Note01Icon : MusicNote01Icon}
                    className="h-6 w-6 text-primary/40"
                  />
                </div>
              </div>
            )}

            {/* Processing indicator */}
            {archive.processing && (
              <div className="absolute inset-0 flex items-center justify-center bg-black/20">
                <div className="rounded-full bg-background/90 p-1.5">
                  <HugeiconsIcon
                    icon={metadataStatus === "paused" ? PauseIcon : Loading03Icon}
                    className={cn(
                      "h-5 w-5",
                      metadataStatus === "paused" ? "text-slate-500" : "animate-spin text-blue-500",
                    )}
                  />
                </div>
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
          <div className={compact ? "flex flex-col gap-0.5 pt-1.5 px-0.5" : "flex flex-col gap-1 pt-2 px-0.5"}>
            <h3 className={compact ? "line-clamp-2 min-h-[2lh] text-[13px] font-medium leading-tight group-hover:text-primary transition-colors" : "line-clamp-2 min-h-[2lh] text-sm font-medium leading-snug group-hover:text-primary transition-colors"}>
              {archive.title}
            </h3>
            <div className={compact ? "flex items-center gap-1.5 text-[11px] text-muted-foreground" : "flex items-center gap-2 text-xs text-muted-foreground"}>
              <span>{archive.date}</span>
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
        <ContextMenuItem disabled={!onCheckpointRerun || checkpointRerunning} onClick={() => onCheckpointRerun?.()}>
          <HugeiconsIcon icon={checkpointRerunning ? Loading03Icon : PlayIcon} className={checkpointRerunning ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
          {checkpointRerunning ? "续做中" : "断点续做"}
        </ContextMenuItem>
        <ContextMenuItem disabled={!onRerun || rerunning} onClick={() => onRerun?.()}>
          <HugeiconsIcon icon={rerunning ? Loading03Icon : RefreshIcon} className={rerunning ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
          {rerunning ? "重做中" : "完整重做"}
        </ContextMenuItem>
        <ContextMenuSeparator />
        {canPause && (
          <ContextMenuItem disabled={taskActionBusy} onClick={() => onPause?.()}>
            <HugeiconsIcon icon={taskActionBusy ? Loading03Icon : PauseIcon} className={taskActionBusy ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
            暂停
          </ContextMenuItem>
        )}
        {canResume && (
          <ContextMenuItem disabled={taskActionBusy} onClick={() => onResume?.()}>
            <HugeiconsIcon icon={taskActionBusy ? Loading03Icon : PlayIcon} className={taskActionBusy ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
            恢复
          </ContextMenuItem>
        )}
        {(canPause || canResume) && <ContextMenuSeparator />}
        <ContextMenuItem onClick={() => setShowRename(true)}>
          <HugeiconsIcon icon={PencilEdit01Icon} className="h-4 w-4" />
          重命名
        </ContextMenuItem>
        <ContextMenuItem variant="destructive" onClick={() => onDelete?.()}>
          <HugeiconsIcon icon={Delete01Icon} className="h-4 w-4" />
          删除
        </ContextMenuItem>
      </ContextMenuContent>
    </ContextMenu>

    <RenameDialog
      open={showRename}
      onOpenChange={setShowRename}
      archivePath={archive.path}
      currentTitle={archive.title}
      onRenamed={(newTitle) => onRenamed?.(newTitle)}
    />
    </>
  )
}
