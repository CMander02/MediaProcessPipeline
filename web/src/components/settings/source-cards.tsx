import { useEffect, useState } from "react"
import { HugeiconsIcon } from "@hugeicons/react"
import { ArrowDown01Icon, ArrowUp01Icon, Loading03Icon, Tick02Icon } from "@hugeicons/core-free-icons"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Label } from "@/components/ui/label"
import { Separator } from "@/components/ui/separator"
import { Switch } from "@/components/ui/switch"
import { api, type Settings } from "@/lib/api"
import { SettingRow } from "./setting-controls"

function ComingSoonBadge() {
  return (
    <Badge variant="outline" className="text-[10px] px-1.5 py-0">即将</Badge>
  )
}

interface PlaceholderSectionProps {
  title: string
  description: string
  comingSoon?: boolean
}

export function PlaceholderSection({
  title,
  description,
  comingSoon = true,
}: PlaceholderSectionProps) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base flex items-center gap-2">
          {title}
          {comingSoon ? (
            <ComingSoonBadge />
          ) : (
            <Badge variant="outline" className="text-[10px] px-1.5 py-0">已支持</Badge>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-sm text-muted-foreground">{description}</p>
      </CardContent>
    </Card>
  )
}

interface ZhihuCardProps {
  settings: Settings
  updateSetting: (key: string, value: unknown) => Promise<void>
  saving: Record<string, boolean>
  saved: Record<string, boolean>
}

export function ZhihuCard({ settings, updateSetting, saving, saved }: ZhihuCardProps) {
  const mode = String(settings.zhihu_browser_mode ?? "background")

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base flex items-center gap-2">
          知乎
          <Badge variant="outline" className="text-[10px] px-1.5 py-0">已支持</Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-5">
        <p className="text-sm text-muted-foreground">
          支持想法和回答链接。知乎反爬较重，优先使用 headless 浏览器；回答页被拦截时会自动用真实浏览器兜底。
        </p>

        <div className="flex items-center gap-3">
          <Label className="w-24 shrink-0 text-sm text-muted-foreground">浏览器兜底</Label>
          <select
            value={mode}
            onChange={(event) => updateSetting("zhihu_browser_mode", event.target.value)}
            className="h-8 rounded-md border border-input bg-background px-2 text-sm"
          >
            <option value="background">后台最小化</option>
            <option value="foreground">前台可见</option>
          </select>
          {saving.zhihu_browser_mode && <span className="text-xs text-muted-foreground">保存中</span>}
          {saved.zhihu_browser_mode && (
            <HugeiconsIcon icon={Tick02Icon} className="h-3.5 w-3.5 text-green-600" />
          )}
        </div>
      </CardContent>
    </Card>
  )
}

interface YoutubeCardProps {
  settings: Settings
  updateSetting: (key: string, value: unknown) => Promise<void>
  saving: Record<string, boolean>
  saved: Record<string, boolean>
}

const YOUTUBE_COOKIE_BROWSERS = [
  "",
  "chrome",
  "firefox",
  "edge",
  "brave",
  "opera",
  "vivaldi",
  "safari",
]

export function YoutubeCard({ settings, updateSetting, saving, saved }: YoutubeCardProps) {
  const browser = String(settings.youtube_cookies_browser ?? "")

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base">YouTube</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-xs text-muted-foreground">
          YouTube 频繁要求登录验证才能下载。任选一种方式提供 cookies：
          指定导出的 cookies.txt，或让 yt-dlp 直接读取浏览器 cookies（Chrome 运行时会锁数据库，建议用 Firefox/Edge 或关闭 Chrome）。
          两者都填则优先使用文件。代理留空时后端会尝试读取系统代理。
        </p>

        <SettingRow
          label="代理"
          settingKey="youtube_proxy"
          value={String(settings.youtube_proxy ?? "")}
          onSave={updateSetting}
          saving={saving}
          saved={saved}
          placeholder="http://127.0.0.1:7897"
        />

        <SettingRow
          label="Cookies 文件"
          settingKey="youtube_cookies_file"
          value={String(settings.youtube_cookies_file ?? "")}
          onSave={updateSetting}
          saving={saving}
          saved={saved}
          placeholder="C:/path/to/cookies.txt"
        />

        <div className="flex items-center gap-3">
          <Label className="w-24 shrink-0 text-sm text-muted-foreground">浏览器</Label>
          <select
            value={browser}
            onChange={(event) => updateSetting("youtube_cookies_browser", event.target.value)}
            className="flex-1 h-8 text-sm rounded-md border border-input bg-background px-2"
          >
            {YOUTUBE_COOKIE_BROWSERS.map((item) => (
              <option key={item} value={item}>{item === "" ? "（不使用）" : item}</option>
            ))}
          </select>
        </div>
      </CardContent>
    </Card>
  )
}

interface XiaohongshuCardProps {
  settings: Settings
  updateSetting: (key: string, value: unknown) => Promise<void>
  saving: Record<string, boolean>
  saved: Record<string, boolean>
}

const XHS_IMAGE_STRATEGIES = [
  {
    id: "browser_interactive",
    label: "浏览器点击采集",
    description: "打开页面，滚动并切换图片后抓取资源",
  },
  {
    id: "browser_request",
    label: "浏览器会话请求",
    description: "使用登录态浏览器上下文请求图片",
  },
  {
    id: "cdn_fallback",
    label: "CDN 原图候选",
    description: "尝试多个小红书图片 CDN 原图地址",
  },
  {
    id: "raw_url",
    label: "页面原始 URL",
    description: "使用页面数据中直接给出的图片地址",
  },
] as const

type XhsImageStrategy = (typeof XHS_IMAGE_STRATEGIES)[number]["id"]

interface XiaohongshuPlatformConfig {
  image_strategy_order: XhsImageStrategy[]
  fail_on_missing_images: boolean
}

function normalizeXhsStrategyOrder(value: unknown): XhsImageStrategy[] {
  const valid = new Set<XhsImageStrategy>(XHS_IMAGE_STRATEGIES.map((item) => item.id))
  const source = Array.isArray(value) ? value : ["raw_url", "cdn_fallback", "browser_request"]
  const order: XhsImageStrategy[] = []
  source.forEach((item) => {
    const id = String(item) as XhsImageStrategy
    if (valid.has(id) && !order.includes(id)) order.push(id)
  })
  XHS_IMAGE_STRATEGIES.forEach((item) => {
    if (!order.includes(item.id)) order.push(item.id)
  })
  return order
}

export function XiaohongshuCard({ settings, updateSetting, saving, saved }: XiaohongshuCardProps) {
  const [status, setStatus] = useState<{
    configured_cookie: boolean
    storage_state_path: string
    storage_state_exists: boolean
    cookie_count: number
    login_cookie: boolean
    updated_at?: string
    error?: string
  } | null>(null)
  const [platformConfig, setPlatformConfig] = useState<XiaohongshuPlatformConfig | null>(null)
  const [savingOrder, setSavingOrder] = useState(false)
  const [savedOrder, setSavedOrder] = useState(false)
  const [loggingIn, setLoggingIn] = useState(false)

  const refreshStatus = () => {
    api.xiaohongshu.status()
      .then(setStatus)
      .catch((error) => {
        setStatus({
          configured_cookie: false,
          storage_state_path: "",
          storage_state_exists: false,
          cookie_count: 0,
          login_cookie: false,
          error: error instanceof Error ? error.message : String(error),
        })
      })
  }

  useEffect(() => {
    refreshStatus()
    api.platforms.list()
      .then((result) => {
        const xhs = result.platforms.find((platform) => platform.id === "xiaohongshu")
        setPlatformConfig({
          image_strategy_order: normalizeXhsStrategyOrder(xhs?.image_strategy_order),
          fail_on_missing_images: Boolean(xhs?.fail_on_missing_images ?? true),
        })
      })
      .catch(() => {
        setPlatformConfig({
          image_strategy_order: normalizeXhsStrategyOrder(undefined),
          fail_on_missing_images: true,
        })
      })
  }, [])

  const handleLogin = async () => {
    if (loggingIn) return
    setLoggingIn(true)
    try {
      const next = await api.xiaohongshu.login(180)
      setStatus(next)
    } catch (error) {
      setStatus((prev) => ({
        configured_cookie: false,
        storage_state_path: "",
        storage_state_exists: false,
        cookie_count: 0,
        login_cookie: false,
        ...prev,
        error: error instanceof Error ? error.message : String(error),
      }))
    } finally {
      setLoggingIn(false)
    }
  }

  const statusLabel = status?.configured_cookie
    ? "Cookie"
    : status?.login_cookie
      ? "已登录"
      : status?.storage_state_exists && (status?.cookie_count ?? 0) > 0
        ? "有会话"
        : "未配置"

  const moveStrategy = async (index: number, delta: -1 | 1) => {
    if (!platformConfig || savingOrder) return
    const nextIndex = index + delta
    if (nextIndex < 0 || nextIndex >= platformConfig.image_strategy_order.length) return
    const nextOrder = [...platformConfig.image_strategy_order]
    const [item] = nextOrder.splice(index, 1)
    nextOrder.splice(nextIndex, 0, item)
    const previous = platformConfig
    setPlatformConfig({ ...platformConfig, image_strategy_order: nextOrder })
    setSavingOrder(true)
    try {
      await api.platforms.update("xiaohongshu", { image_strategy_order: nextOrder })
      setSavedOrder(true)
      setTimeout(() => setSavedOrder(false), 1500)
    } catch {
      setPlatformConfig(previous)
    } finally {
      setSavingOrder(false)
    }
  }

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base flex items-center gap-2">
          小红书
          <Badge variant="outline" className="text-[10px] px-1.5 py-0">{statusLabel}</Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <SettingRow
          label="Cookie"
          settingKey="xiaohongshu_cookie"
          value={String(settings.xiaohongshu_cookie ?? "")}
          onSave={updateSetting}
          saving={saving}
          saved={saved}
          masked
          placeholder="web_session=...; a1=..."
        />
        <SettingRow
          label="登录态文件"
          settingKey="xiaohongshu_storage_state_path"
          value={String(settings.xiaohongshu_storage_state_path ?? "")}
          onSave={updateSetting}
          saving={saving}
          saved={saved}
          placeholder="留空使用 data/auth/xiaohongshu_storage_state.json"
        />
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <span>Cookies: {status?.cookie_count ?? 0}</span>
          {status?.updated_at && <span>{new Date(status.updated_at).toLocaleString()}</span>}
          {status?.error && <span className="text-destructive">{status.error}</span>}
        </div>
        <div className="flex items-center gap-2">
          <Button type="button" size="sm" onClick={handleLogin} disabled={loggingIn}>
            {loggingIn && <HugeiconsIcon icon={Loading03Icon} className="h-4 w-4 animate-spin" />}
            打开登录
          </Button>
          <Button type="button" size="sm" variant="outline" onClick={refreshStatus}>
            刷新状态
          </Button>
        </div>
        <Separator />
        <div className="space-y-3">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-xs font-medium text-muted-foreground">图片获取优先级</p>
              <p className="mt-0.5 text-xs text-muted-foreground">从上到下依次尝试，缺图会直接失败。</p>
            </div>
            <div className="flex items-center gap-1 text-xs text-muted-foreground">
              {platformConfig?.fail_on_missing_images && (
                <Badge variant="outline" className="px-1.5 py-0 text-[10px]">缺图失败</Badge>
              )}
              {savingOrder && <span>保存中</span>}
              {savedOrder && <HugeiconsIcon icon={Tick02Icon} className="h-3.5 w-3.5 text-emerald-500" />}
            </div>
          </div>
          <div className="space-y-2">
            {(platformConfig?.image_strategy_order ?? normalizeXhsStrategyOrder(undefined)).map((id, index, order) => {
              const item = XHS_IMAGE_STRATEGIES.find((strategy) => strategy.id === id)
              if (!item) return null
              return (
                <div
                  key={id}
                  className="flex items-center gap-2 rounded-md border border-border/70 bg-background px-2 py-2"
                >
                  <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-medium text-primary">
                    {index + 1}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium">{item.label}</div>
                    <div className="truncate text-xs text-muted-foreground">{item.description}</div>
                  </div>
                  <div className="flex items-center gap-1">
                    <Button
                      type="button"
                      size="icon"
                      variant="ghost"
                      className="h-7 w-7"
                      aria-label={`${item.label} 上移`}
                      disabled={index === 0 || savingOrder}
                      onClick={() => moveStrategy(index, -1)}
                    >
                      <HugeiconsIcon icon={ArrowUp01Icon} className="h-3.5 w-3.5" />
                    </Button>
                    <Button
                      type="button"
                      size="icon"
                      variant="ghost"
                      className="h-7 w-7"
                      aria-label={`${item.label} 下移`}
                      disabled={index === order.length - 1 || savingOrder}
                      onClick={() => moveStrategy(index, 1)}
                    >
                      <HugeiconsIcon icon={ArrowDown01Icon} className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

const BILIBILI_QUALITY_OPTIONS = [
  { value: 16, label: "360P（无需登录）" },
  { value: 32, label: "480P" },
  { value: 64, label: "720P（推荐）" },
  { value: 80, label: "1080P" },
]

interface BilibiliCardProps {
  onAuthChange?: (loggedIn: boolean) => void
}

interface BilibiliPlatformConfig {
  preferred_quality: number
  prefer_subtitle: boolean
  subtitle_engine: string
  subtitle_languages: string
  subtitle_strict_validation: boolean
  subtitle_min_coverage: number
  subtitle_allow_legacy_fallback: boolean
}

export function BilibiliCard({ onAuthChange }: BilibiliCardProps) {
  const [status, setStatus] = useState<{
    logged_in: boolean
    uid?: string
    expires?: string
    days_left?: number
    message?: string
  } | null>(null)
  const [platformConfig, setPlatformConfig] = useState<BilibiliPlatformConfig | null>(null)
  const [savedQuality, setSavedQuality] = useState(false)
  const [savedSubtitle, setSavedSubtitle] = useState(false)
  const [savingPlatform, setSavingPlatform] = useState<Record<string, boolean>>({})
  const [savedPlatform, setSavedPlatform] = useState<Record<string, boolean>>({})

  useEffect(() => {
    api.bilibili.status()
      .then((nextStatus) => {
        setStatus(nextStatus)
        onAuthChange?.(nextStatus.logged_in)
      })
      .catch(() => {
        setStatus({ logged_in: false, message: "无法连接后端" })
        onAuthChange?.(false)
      })

    api.platforms.list().then((result) => {
      const bili = result.platforms.find((platform) => platform.id === "bilibili")
      if (bili) {
        setPlatformConfig({
          preferred_quality: Number(bili.preferred_quality ?? 64),
          prefer_subtitle: Boolean(bili.prefer_subtitle),
          subtitle_engine: String(bili.subtitle_engine ?? "native_wbi"),
          subtitle_languages: String(bili.subtitle_languages ?? "zh,en"),
          subtitle_strict_validation: Boolean(bili.subtitle_strict_validation ?? true),
          subtitle_min_coverage: Number(bili.subtitle_min_coverage ?? 0.6),
          subtitle_allow_legacy_fallback: Boolean(bili.subtitle_allow_legacy_fallback ?? false),
        })
      }
    }).catch(() => {
      setPlatformConfig(null)
    })
  }, [onAuthChange])

  const handleQualityChange = async (value: number) => {
    if (!platformConfig) return
    setPlatformConfig((prev) => prev ? { ...prev, preferred_quality: value } : prev)
    try {
      await api.platforms.update("bilibili", { preferred_quality: value })
      setSavedQuality(true)
      setTimeout(() => setSavedQuality(false), 1500)
    } catch {
      setPlatformConfig(platformConfig)
    }
  }

  const handleSubtitleChange = async (checked: boolean) => {
    if (!platformConfig) return
    setPlatformConfig((prev) => prev ? { ...prev, prefer_subtitle: checked } : prev)
    try {
      await api.platforms.update("bilibili", { prefer_subtitle: checked })
      setSavedSubtitle(true)
      setTimeout(() => setSavedSubtitle(false), 1500)
    } catch {
      setPlatformConfig(platformConfig)
    }
  }

  const updatePlatformSetting = async (key: string, value: unknown) => {
    if (!platformConfig) return
    setPlatformConfig((prev) => prev ? { ...prev, [key]: value } : prev)
    setSavingPlatform((prev) => ({ ...prev, [key]: true }))
    try {
      await api.platforms.update("bilibili", { [key]: value })
      setSavedPlatform((prev) => ({ ...prev, [key]: true }))
      setTimeout(() => setSavedPlatform((prev) => ({ ...prev, [key]: false })), 1500)
    } catch {
      setPlatformConfig(platformConfig)
    } finally {
      setSavingPlatform((prev) => ({ ...prev, [key]: false }))
    }
  }

  const loggedIn = status?.logged_in ?? false

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base">哔哩哔哩</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {status === null ? (
          <p className="text-sm text-muted-foreground">加载中...</p>
        ) : status.logged_in ? (
          <div className="space-y-1 text-sm">
            <div className="flex items-center gap-2">
              <span className="h-2 w-2 rounded-full bg-emerald-500" />
              <span>已登录 (UID: {status.uid})</span>
            </div>
            <p className="text-muted-foreground">
              Cookie 有效期至 {status.expires?.split("T")[0]}（剩余 {status.days_left} 天）
            </p>
          </div>
        ) : (
          <div className="space-y-2 text-sm">
            <div className="flex items-center gap-2">
              <span className="h-2 w-2 rounded-full bg-red-500" />
              <span>未登录</span>
            </div>
            <p className="text-muted-foreground">
              {status.message ?? "请在终端运行 BBDown.exe login 扫码登录"}
            </p>
            <p className="text-xs text-muted-foreground">
              路径: backend/tools/bbdown/BBDown.exe login
            </p>
          </div>
        )}

        {platformConfig !== null && (
          <>
            <Separator />
            <div className="space-y-3">
              <p className="text-xs font-medium text-muted-foreground">下载设置</p>

              <div className="flex items-center gap-3">
                <Label className="w-24 shrink-0 text-sm text-muted-foreground">首选清晰度</Label>
                <select
                  value={loggedIn ? platformConfig.preferred_quality : 16}
                  disabled={!loggedIn}
                  onChange={(event) => handleQualityChange(Number(event.target.value))}
                  className="flex-1 h-8 rounded-md border border-input bg-background px-2 text-sm disabled:opacity-50"
                >
                  {BILIBILI_QUALITY_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>{option.label}</option>
                  ))}
                </select>
                {savedQuality && (
                  <HugeiconsIcon icon={Tick02Icon} className="h-3.5 w-3.5 text-emerald-500" />
                )}
              </div>
              {!loggedIn && (
                <p className="text-xs text-muted-foreground pl-[7.5rem]">（未登录时固定 360P）</p>
              )}

              <div className="flex items-center justify-between">
                <Label className="text-sm text-muted-foreground">优先使用平台字幕</Label>
                <div className="flex items-center gap-2">
                  {savedSubtitle && (
                    <HugeiconsIcon icon={Tick02Icon} className="h-3.5 w-3.5 text-emerald-500" />
                  )}
                  <Switch
                    checked={platformConfig.prefer_subtitle}
                    onCheckedChange={handleSubtitleChange}
                  />
                </div>
              </div>

              <Separator />
              <p className="text-xs font-medium text-muted-foreground">字幕下载与校验</p>

              <div className="flex items-center gap-3">
                <Label className="w-24 shrink-0 text-sm text-muted-foreground">字幕引擎</Label>
                <select
                  value={platformConfig.subtitle_engine}
                  onChange={(event) => updatePlatformSetting("subtitle_engine", event.target.value)}
                  className="flex-1 h-8 rounded-md border border-input bg-background px-2 text-sm"
                >
                  <option value="native_wbi">原生 WBI API（非 yt-dlp）</option>
                </select>
                {savedPlatform.subtitle_engine && (
                  <HugeiconsIcon icon={Tick02Icon} className="h-3.5 w-3.5 text-emerald-500" />
                )}
              </div>

              <SettingRow
                label="语言优先级"
                settingKey="subtitle_languages"
                value={platformConfig.subtitle_languages}
                onSave={updatePlatformSetting}
                saving={savingPlatform}
                saved={savedPlatform}
                placeholder="zh,en,ja"
              />

              <div className="flex items-center justify-between">
                <div>
                  <Label className="text-sm text-muted-foreground">严格防串字幕校验</Label>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    校验字幕 blob 文件名是否匹配当前视频 aid+cid，不匹配则跳过并回退 ASR。
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  {savedPlatform.subtitle_strict_validation && (
                    <HugeiconsIcon icon={Tick02Icon} className="h-3.5 w-3.5 text-emerald-500" />
                  )}
                  <Switch
                    checked={platformConfig.subtitle_strict_validation}
                    onCheckedChange={(value) =>
                      updatePlatformSetting("subtitle_strict_validation", Boolean(value))}
                  />
                </div>
              </div>

              <div className="flex items-center gap-3">
                <Label className="w-24 shrink-0 text-sm text-muted-foreground">最低覆盖率</Label>
                <select
                  value={String(platformConfig.subtitle_min_coverage)}
                  onChange={(event) =>
                    updatePlatformSetting("subtitle_min_coverage", Number(event.target.value))}
                  className="h-8 rounded-md border border-input bg-background px-2 text-sm"
                >
                  <option value="0.5">50%</option>
                  <option value="0.6">60%（推荐）</option>
                  <option value="0.7">70%</option>
                  <option value="0.8">80%</option>
                </select>
                {savedPlatform.subtitle_min_coverage && (
                  <HugeiconsIcon icon={Tick02Icon} className="h-3.5 w-3.5 text-emerald-500" />
                )}
              </div>

              <div className="flex items-center justify-between">
                <div>
                  <Label className="text-sm text-muted-foreground">允许旧接口回退</Label>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    仅在原生 WBI 模块不可用时启用；旧接口更容易出现串字幕，默认关闭。
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  {savedPlatform.subtitle_allow_legacy_fallback && (
                    <HugeiconsIcon icon={Tick02Icon} className="h-3.5 w-3.5 text-emerald-500" />
                  )}
                  <Switch
                    checked={platformConfig.subtitle_allow_legacy_fallback}
                    onCheckedChange={(value) =>
                      updatePlatformSetting("subtitle_allow_legacy_fallback", Boolean(value))}
                  />
                </div>
              </div>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}
