import { HugeiconsIcon } from "@hugeicons/react"
import {
  FolderOpenIcon,
  Image01Icon,
  MusicNote01Icon,
  Note01Icon,
  Video01Icon,
} from "@hugeicons/core-free-icons"
import { PlatformIcon } from "@/components/platform-icon"
import { getPlatformLabel, normalizeSourceFilter } from "@/lib/archive-filters"
import { cn } from "@/lib/utils"

const PLATFORM_SURFACE_STYLES: Record<string, string> = {
  zhihu: "from-sky-500/12 via-blue-500/8 to-transparent dark:from-sky-400/18 dark:via-blue-500/10",
  xiaoyuzhou: "from-violet-500/12 via-indigo-500/8 to-transparent dark:from-violet-400/18 dark:via-indigo-500/10",
  xiaohongshu: "from-rose-500/12 via-pink-500/8 to-transparent dark:from-rose-400/18 dark:via-pink-500/10",
  apple_podcast: "from-purple-500/12 via-fuchsia-500/8 to-transparent dark:from-purple-400/18 dark:via-fuchsia-500/10",
  bilibili: "from-cyan-500/12 via-sky-500/8 to-transparent dark:from-cyan-400/18 dark:via-sky-500/10",
  youtube: "from-red-500/12 via-orange-500/6 to-transparent dark:from-red-400/18 dark:via-orange-500/8",
  x: "from-slate-500/12 via-zinc-500/8 to-transparent dark:from-slate-400/18 dark:via-zinc-500/10",
  webpage: "from-emerald-500/10 via-teal-500/6 to-transparent dark:from-emerald-400/16 dark:via-teal-500/8",
  local: "from-amber-500/10 via-stone-500/8 to-transparent dark:from-amber-400/16 dark:via-stone-500/10",
}

function sourceHostname(sourceUrl?: string | null): string | null {
  if (!sourceUrl) return null
  try {
    return new URL(sourceUrl).hostname.replace(/^www\./, "")
  } catch {
    return null
  }
}

export interface ArchiveCoverFallbackProps {
  title?: string
  platform?: string | null
  uploader?: string | null
  sourceUrl?: string | null
  hintText?: string | null
  mediaLabel: string
  isVideo?: boolean
  isImageNote?: boolean
  isTextNote?: boolean
  variant?: "card" | "mini"
  className?: string
}

export function ArchiveCoverFallback({
  title,
  platform,
  uploader,
  sourceUrl,
  hintText,
  mediaLabel,
  isVideo = false,
  isImageNote = false,
  isTextNote = false,
  variant = "card",
  className,
}: ArchiveCoverFallbackProps) {
  const sourceKey = platform ? normalizeSourceFilter(platform) : "other"
  const isLocal = sourceKey === "local"
  const surfaceGradient = PLATFORM_SURFACE_STYLES[sourceKey] ?? "from-primary/8 via-muted/60 to-transparent"
  const fallbackMediaIcon = isVideo ? Video01Icon : isImageNote ? Image01Icon : isTextNote ? Note01Icon : MusicNote01Icon
  const host = sourceHostname(sourceUrl)
  const sourceLabel = getPlatformLabel(platform) ?? host ?? (isLocal ? "本地文件" : null)
  const cleanedHint = (hintText?.trim() && hintText.trim() !== title?.trim())
    ? hintText.trim()
    : (uploader?.trim() || (host && sourceLabel !== host ? host : null))

  const sourceGlyph = (sizeClass: string) => isLocal ? (
    <HugeiconsIcon
      icon={FolderOpenIcon}
      className={cn(sizeClass, "shrink-0 text-foreground/75")}
      strokeWidth={1.75}
    />
  ) : platform ? (
    <PlatformIcon
      platform={platform}
      iconOnly
      className={cn(sizeClass, "shrink-0")}
    />
  ) : (
    <HugeiconsIcon
      icon={fallbackMediaIcon}
      className={cn(sizeClass, "shrink-0 text-muted-foreground/75")}
      strokeWidth={1.75}
    />
  )

  if (variant === "mini") {
    return (
      <div
        className={cn(
          "flex h-full w-full items-center justify-center bg-muted bg-gradient-to-br",
          surfaceGradient,
          className,
        )}
      >
        {sourceGlyph("h-4 w-4")}
      </div>
    )
  }

  return (
    <div
      className={cn(
        "flex h-full w-full select-none flex-col justify-between overflow-hidden bg-muted/70 bg-gradient-to-br p-2.5 text-left",
        surfaceGradient,
        className,
      )}
    >
      <div className="flex min-w-0 items-center justify-between gap-2">
        <span className="flex min-w-0 items-center gap-1.5">
          {sourceLabel ? (
            <span className="truncate text-[11px] font-medium text-foreground/75">{sourceLabel}</span>
          ) : null}
        </span>
        <span className="shrink-0 rounded bg-background/75 px-1.5 py-0.5 text-[10px] font-medium leading-none text-muted-foreground backdrop-blur-[2px]">
          {mediaLabel}
        </span>
      </div>

      <div className="flex flex-1 items-center justify-center py-1">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-background/70 shadow-xs ring-1 ring-border/40 backdrop-blur-[2px]">
          {sourceGlyph("h-6 w-6")}
        </div>
      </div>

      <div className="min-w-0 min-h-3.5">
        {cleanedHint ? (
          <p className="truncate pr-12 text-[11px] leading-tight text-foreground/70">
            {cleanedHint}
          </p>
        ) : null}
      </div>
    </div>
  )
}
