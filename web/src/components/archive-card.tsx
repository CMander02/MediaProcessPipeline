import { useState } from "react"
import type { ArchiveItem } from "@/hooks/use-archives"
import { formatDuration } from "@/lib/format"
import { api } from "@/lib/api"
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from "@/components/ui/context-menu"
import { RenameDialog } from "@/components/rename-dialog"
import { HugeiconsIcon } from "@hugeicons/react"
import { Video01Icon, MusicNote01Icon, Note01Icon, FolderOpenIcon, PencilEdit01Icon, Delete01Icon, Loading03Icon } from "@hugeicons/core-free-icons"

interface ArchiveCardProps {
  archive: ArchiveItem
  onClick: () => void
  onDelete?: () => void
  onRenamed?: (newTitle: string) => void
}

export function ArchiveCard({ archive, onClick, onDelete, onRenamed }: ArchiveCardProps) {
  const [imgError, setImgError] = useState(false)
  const [showRename, setShowRename] = useState(false)

  const showThumbnail = archive.has_video && !imgError
  const thumbnailUrl = archive.has_video
    ? api.archives.thumbnailUrl(archive.path)
    : null

  return (
    <>
    <ContextMenu>
      <ContextMenuTrigger>
        <button
          onClick={onClick}
          className="group flex flex-col text-left w-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-lg"
        >
          {/* Thumbnail */}
          <div className="relative aspect-video w-full rounded-lg overflow-hidden bg-muted">
            {showThumbnail && thumbnailUrl ? (
              <img
                src={thumbnailUrl}
                alt=""
                loading="lazy"
                onError={() => setImgError(true)}
                className="h-full w-full object-cover transition-transform group-hover:scale-[1.03]"
              />
            ) : archive.has_video ? (
              <div className="flex h-full w-full items-center justify-center">
                <HugeiconsIcon icon={Video01Icon} className="h-8 w-8 text-muted-foreground/30" />
              </div>
            ) : (
              <div className="flex h-full w-full items-center justify-center bg-gradient-to-br from-primary/5 to-primary/15">
                <div className="rounded-full bg-primary/10 p-3">
                  <HugeiconsIcon icon={MusicNote01Icon} className="h-6 w-6 text-primary/40" />
                </div>
              </div>
            )}

            {/* Processing indicator */}
            {archive.processing && (
              <div className="absolute inset-0 flex items-center justify-center bg-black/20">
                <div className="rounded-full bg-background/90 p-1.5">
                  <HugeiconsIcon icon={Loading03Icon} className="h-5 w-5 animate-spin text-blue-500" />
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
          <div className="flex flex-col gap-1 pt-2 px-0.5">
            <h3 className="line-clamp-2 text-sm font-medium leading-snug group-hover:text-primary transition-colors">
              {archive.title}
            </h3>
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <span>{archive.date}</span>
              <span className="flex items-center gap-0.5">
                {archive.has_video ? <HugeiconsIcon icon={Video01Icon} className="h-3 w-3" /> : <HugeiconsIcon icon={MusicNote01Icon} className="h-3 w-3" />}
                {archive.has_video ? "视频" : "音频"}
              </span>
              {archive.has_summary && (
                <span className="flex items-center gap-0.5">
                  <HugeiconsIcon icon={Note01Icon} className="h-3 w-3" />摘要
                </span>
              )}
              {typeof archive.metadata?.platform === "string" && (
                <span className="rounded bg-emerald-500/15 px-1 py-0.5 text-[10px] font-medium text-emerald-700 dark:text-emerald-400">
                  {archive.metadata.platform as string}
                </span>
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
        <ContextMenuItem onClick={() => setShowRename(true)}>
          <HugeiconsIcon icon={PencilEdit01Icon} className="h-4 w-4" />
          重命名
        </ContextMenuItem>
        <ContextMenuSeparator />
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
