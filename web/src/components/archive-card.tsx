import { useState } from "react"
import type { ArchiveItem } from "@/hooks/use-archives"
import { formatDuration } from "@/lib/format"
import { Badge } from "@/components/ui/badge"
import { Video, Music, FileText, Clock } from "lucide-react"

interface ArchiveCardProps {
  archive: ArchiveItem
  onClick: () => void
}

export function ArchiveCard({ archive, onClick }: ArchiveCardProps) {
  const [imgError, setImgError] = useState(false)

  // Only attempt thumbnail for video archives
  const showThumbnail = archive.has_video && !imgError
  const thumbnailUrl = archive.has_video
    ? `/api/pipeline/archives/thumbnail?path=${encodeURIComponent(archive.path)}`
    : null

  const contentType = archive.analysis?.content_type
  const mediaIcon = archive.has_video ? (
    <Video className="h-3.5 w-3.5" />
  ) : (
    <Music className="h-3.5 w-3.5" />
  )

  return (
    <button
      onClick={onClick}
      className="group relative flex flex-col overflow-hidden rounded-lg border bg-card text-left transition-all hover:shadow-md hover:border-primary/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      {/* Thumbnail area */}
      <div className="relative aspect-video w-full bg-muted overflow-hidden">
        {showThumbnail && thumbnailUrl ? (
          <img
            src={thumbnailUrl}
            alt=""
            loading="lazy"
            onError={() => setImgError(true)}
            className="h-full w-full object-cover transition-transform group-hover:scale-[1.03]"
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center">
            {archive.has_video ? (
              <Video className="h-8 w-8 text-muted-foreground/30" />
            ) : (
              <Music className="h-8 w-8 text-muted-foreground/30" />
            )}
          </div>
        )}

        {/* Duration badge */}
        {archive.duration_seconds != null && archive.duration_seconds > 0 && (
          <span className="absolute bottom-1.5 right-1.5 rounded bg-black/70 px-1.5 py-0.5 text-[11px] font-medium text-white tabular-nums">
            {formatDuration(archive.duration_seconds)}
          </span>
        )}
      </div>

      {/* Info area */}
      <div className="flex flex-1 flex-col gap-1.5 p-3">
        <h3 className="line-clamp-2 text-sm font-medium leading-snug">{archive.title}</h3>

        <div className="mt-auto flex items-center gap-2 text-xs text-muted-foreground">
          <span className="flex items-center gap-1">
            <Clock className="h-3 w-3" />
            {archive.date}
          </span>
          <span className="flex items-center gap-1">{mediaIcon}{archive.has_video ? "视频" : "音频"}</span>
          {archive.has_summary && (
            <span className="flex items-center gap-1">
              <FileText className="h-3 w-3" />摘要
            </span>
          )}
        </div>

        {/* Content type / topics */}
        {(contentType || (archive.analysis?.main_topics?.length ?? 0) > 0) && (
          <div className="flex flex-wrap gap-1 mt-1">
            {contentType && (
              <Badge variant="secondary" className="text-[10px] px-1.5 py-0">
                {contentType}
              </Badge>
            )}
            {archive.analysis?.main_topics?.slice(0, 2).map((t) => (
              <Badge key={t} variant="outline" className="text-[10px] px-1.5 py-0">
                {t}
              </Badge>
            ))}
          </div>
        )}
      </div>
    </button>
  )
}
