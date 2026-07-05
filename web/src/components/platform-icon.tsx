import applePodcastsSvg from "@/assets/platform-icons/apple-podcasts.svg"
import bilibiliSvg from "@/assets/platform-icons/bilibili.svg"
import webpageSvg from "@/assets/platform-icons/webpage.svg"
import xTwitterSvg from "@/assets/platform-icons/x-twitter.svg"
import xiaohongshuSvg from "@/assets/platform-icons/xiaohongshu.svg"
import youtubeSvg from "@/assets/platform-icons/youtube.svg"
import zhihuSvg from "@/assets/platform-icons/zhihu.svg"

const PLATFORM_ICONS: Record<string, { src: string; label: string }> = {
  apple_podcast: { src: applePodcastsSvg, label: "Apple Podcasts" },
  apple: { src: applePodcastsSvg, label: "Apple Podcasts" },
  bilibili: { src: bilibiliSvg, label: "Bilibili" },
  bilibili_opus: { src: bilibiliSvg, label: "Bilibili" },
  bilibili_video: { src: bilibiliSvg, label: "Bilibili" },
  bili: { src: bilibiliSvg, label: "Bilibili" },
  webpage: { src: webpageSvg, label: "Webpage" },
  web: { src: webpageSvg, label: "Webpage" },
  generic_webpage: { src: webpageSvg, label: "Webpage" },
  twitter: { src: xTwitterSvg, label: "X" },
  x: { src: xTwitterSvg, label: "X" },
  x_twitter: { src: xTwitterSvg, label: "X" },
  xiaohongshu: { src: xiaohongshuSvg, label: "小红书" },
  xhs: { src: xiaohongshuSvg, label: "小红书" },
  youtube: { src: youtubeSvg, label: "YouTube" },
  yt: { src: youtubeSvg, label: "YouTube" },
  zhihu: { src: zhihuSvg, label: "知乎" },
}

const PLATFORM_LABELS: Record<string, string> = {
  xiaoyuzhou: "小宇宙",
}

interface PlatformIconProps {
  platform: string | null | undefined
  className?: string
  uploader?: string | null
  /** Force fallback text rendering off (icon-only mode). */
  iconOnly?: boolean
}

/**
 * Renders a brand-color SVG icon for the platform when available.
 * Falls back to a small text badge for platforms without an icon (e.g. xiaoyuzhou).
 */
export function PlatformIcon({ platform, className, uploader, iconOnly }: PlatformIconProps) {
  if (!platform) return null
  const key = platform.toLowerCase()
  const entry = PLATFORM_ICONS[key]
  const title = uploader ? `${entry?.label ?? PLATFORM_LABELS[key] ?? platform} · ${uploader}` : entry?.label ?? PLATFORM_LABELS[key] ?? platform

  if (entry) {
    return (
      <img
        src={entry.src}
        alt={entry.label}
        title={title}
        className={className ?? "h-3.5 w-3.5 shrink-0"}
        draggable={false}
      />
    )
  }

  if (iconOnly) return null

  return (
    <span
      className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground"
      title={title}
    >
      {PLATFORM_LABELS[key] ?? platform}
    </span>
  )
}
