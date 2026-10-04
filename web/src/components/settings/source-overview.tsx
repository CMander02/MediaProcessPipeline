import type { BilibiliAuthStatus, Settings, TwitterAuthStatus, XiaohongshuAuthStatus } from "@/lib/api"
import { cn } from "@/lib/utils"

type Tone = "ok" | "warn" | "quiet"

interface SourceState {
  label: string
  tone: Tone
}

const CHECKING: SourceState = { label: "检测中…", tone: "quiet" }
const NO_LOGIN: SourceState = { label: "无需登录", tone: "quiet" }

function bilibiliState(status: BilibiliAuthStatus | null): SourceState {
  if (!status) return CHECKING
  if (status.logged_in) {
    return status.days_left != null && status.days_left <= 7
      ? { label: `${status.days_left} 天后过期`, tone: "warn" }
      : { label: "已登录", tone: "ok" }
  }
  if (status.message?.includes("过期")) return { label: "已过期", tone: "warn" }
  // A saved cookie with an expiry date that the site no longer accepts
  if (status.expires) return { label: "登录已失效", tone: "warn" }
  return { label: "未登录", tone: "warn" }
}

function youtubeState(settings: Settings): SourceState {
  const configured = String(settings.youtube_cookies_file ?? "").trim() || String(settings.youtube_cookies_browser ?? "").trim()
  return configured ? { label: "已配置 cookies", tone: "ok" } : { label: "未提供 cookies", tone: "warn" }
}

function twitterState(status: TwitterAuthStatus | null): SourceState {
  if (!status) return CHECKING
  return status.logged_in ? { label: "已登录", tone: "ok" } : { label: "未登录 · 长文需要", tone: "quiet" }
}

function xiaohongshuState(status: XiaohongshuAuthStatus | null): SourceState {
  if (!status) return CHECKING
  if (status.configured_cookie) return { label: "已配置 Cookie", tone: "ok" }
  if (status.login_cookie) return { label: "已登录", tone: "ok" }
  if (status.storage_state_exists && status.cookie_count > 0) return { label: "已保存会话", tone: "ok" }
  return { label: "未登录 · 可选", tone: "quiet" }
}

const DOT: Record<Tone, string> = {
  ok: "bg-emerald-500",
  warn: "bg-amber-500",
  quiet: "bg-muted-foreground/40",
}

/** Every source's login state at a glance; a tile opens that source's settings below. */
export function SourceOverview({
  settings,
  bilibili,
  twitter,
  xiaohongshu,
  onOpen,
}: {
  settings: Settings
  bilibili: BilibiliAuthStatus | null
  twitter: TwitterAuthStatus | null
  xiaohongshu: XiaohongshuAuthStatus | null
  onOpen: (sectionId: string) => void
}) {
  const sources: Array<{ id: string; title: string; state: SourceState }> = [
    { id: "bilibili", title: "哔哩哔哩", state: bilibiliState(bilibili) },
    { id: "youtube", title: "YouTube", state: youtubeState(settings) },
    { id: "twitter", title: "X", state: twitterState(twitter) },
    { id: "xiaoyuzhou", title: "小宇宙", state: NO_LOGIN },
    { id: "xiaohongshu", title: "小红书", state: xiaohongshuState(xiaohongshu) },
    { id: "zhihu", title: "知乎", state: NO_LOGIN },
  ]
  return (
    <ul aria-label="来源状态" className="grid grid-cols-2 gap-2 sm:grid-cols-3">
      {sources.map(({ id, title, state }) => (
        <li key={id}>
          <button
            type="button"
            onClick={() => onOpen(id)}
            className="flex w-full flex-col items-start gap-1 rounded-lg border bg-card px-3 py-2.5 text-left transition-colors hover:bg-muted"
          >
            <span className="text-sm font-medium">{title}</span>
            <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <span aria-hidden className={cn("size-1.5 shrink-0 rounded-full", DOT[state.tone])} />
              <span className={cn(state.tone === "warn" && "text-amber-700 dark:text-amber-400")}>{state.label}</span>
            </span>
          </button>
        </li>
      ))}
    </ul>
  )
}
