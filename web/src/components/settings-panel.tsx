import { useCallback, useEffect, useRef, useState, type ReactNode } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group"
import { Label } from "@/components/ui/label"
import { Separator } from "@/components/ui/separator"
import { Switch } from "@/components/ui/switch"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { api, type BilibiliAuthStatus, type Settings, type TwitterAuthStatus, type XiaohongshuAuthStatus, type YtdlpStatus } from "@/lib/api"
import { usePreferences } from "@/hooks/use-preferences"
import { systemNotificationsAvailable } from "@/hooks/use-task-notifications"
import { OptionSelect, ProxySetting, SettingRow } from "@/components/settings/setting-controls"
import { LibraryLocation } from "@/components/settings/library-location"
import { SourceOverview } from "@/components/settings/source-overview"
import { SettingsSaveErrors } from "@/components/settings/save-errors"
import { notifyError } from "@/lib/notify"
import { describeProxy } from "@/lib/proxy"
import { LocalModelSection, ProviderSummary, PurposeModelBindings, RegistrySettings } from "@/components/settings/model-sections"
import { BilibiliCard, PlaceholderSection, TwitterCard, XiaohongshuCard, YoutubeCard, ZhihuCard } from "@/components/settings/source-cards"
import { HugeiconsIcon } from "@hugeicons/react"
import { ArrowLeft01Icon, ArrowRight01Icon, Loading03Icon, Moon02Icon, Search01Icon, Sun01Icon, Tick02Icon } from "@hugeicons/core-free-icons"
import { NativeConnectionSettings } from "@/components/native-connection"
import { OfflineSyncStatus } from "@/components/offline-sync-status"
import { usePlatform } from "@/platform/use-platform"
import { getThemePreference, setThemePreference, type ThemePreference } from "@/lib/theme"
import { useAppAccess } from "@/hooks/use-app-access-context"
import { mediaPolicies } from "@/lib/media-retention"
import { buildHash, navigate, useRoute } from "@/lib/router"

// --- Groups ---

type GroupId = "general" | "pipeline" | "providers" | "sources" | "storage" | "advanced"

interface SectionDef {
  id: string
  title: string
  /** What is inside, for the settings search */
  keywords?: string
  /** Editable on phones and in the Android app; there the rest is shown read-only */
  mobile?: boolean
}

interface GroupDef {
  id: GroupId
  label: string
  description: string
  sections: SectionDef[]
}

// Grouped by the decision being made; 处理流程 follows the order a task goes through.
const GROUPS: GroupDef[] = [
  {
    id: "general",
    label: "常规",
    description: "外观、启动页和日常使用的习惯。",
    sections: [
      { id: "appearance", title: "外观", mobile: true, keywords: "主题 深色 浅色 跟随系统 暗色 外观" },
      { id: "startup", title: "启动页面", mobile: true, keywords: "启动页面 文件列表 上次打开的归档" },
      { id: "playback", title: "播放", mobile: true, keywords: "循环播放 视频 音频" },
      { id: "intake", title: "新建处理", mobile: true, keywords: "剪贴板 链接 粘贴 拖入 Ctrl+N 完成或失败时通知 系统通知 提醒" },
    ],
  },
  {
    id: "pipeline",
    label: "处理流程",
    description: "新任务按这个顺序处理：处理方式、人声分离、语音识别，再交给模型润色、分析和总结。每一步用哪个模型，在这里决定。",
    sections: [
      { id: "audio-flow", title: "音频流程", keywords: "默认处理方式 标准语音识别 MOSS 一体化识别 说话人分离 SPEAKER" },
      { id: "uvr", title: "人声分离", keywords: "人声分离 UVR 背景音乐 默认模型 模型目录 运行设备 分段 检查本机 UVR" },
      { id: "asr", title: "语音识别", keywords: "语音识别 ASR 服务 llama.cpp Qwen3-ASR sherpa-onnx SiliconFlow 时间戳 VAD 强制对齐 ForcedAligner pyannote 说话人模型 分段并发 GGUF mmproj" },
      { id: "models", title: "各步骤的模型", keywords: "模型用途 字幕简单润色 字幕二次润色 字幕分析 全文总结 思维导图 图文理解 知识库向量 润色并发数 绑定" },
      { id: "local-llm", title: "本地大模型", keywords: "本地大模型 LLM 推理引擎 GGUF Transformers Hugging Face 运行设备 上下文 数据类型 显存" },
      { id: "inference", title: "推理模式", keywords: "推理模式 半重叠推理 显存 detail.md 视频详情" },
      { id: "knowledge", title: "知识库索引", mobile: true, keywords: "知识库 Embedding 向量 自动索引 API 地址 API Base API Key 模型 向量维度" },
    ],
  },
  {
    id: "providers",
    label: "模型服务商",
    description: "API 和 OAuth 服务商，以及它们提供的模型。",
    sections: [{ id: "providers", title: "模型服务商", keywords: "模型服务商 服务商 Provider API Key API 地址 API Base 接口模式 DeepSeek SiliconFlow OpenAI Anthropic OAuth Codex Kimi Qoder 获取模型 同步模型 添加模型" }],
  },
  {
    id: "sources",
    label: "来源与账号",
    description: "各平台的登录状态、下载和字幕设置。",
    sections: [
      { id: "bilibili", title: "哔哩哔哩", keywords: "哔哩哔哩 B站 扫码登录 Cookie 首选清晰度 平台字幕 字幕引擎 WBI 语言优先级 防串字幕 覆盖率 旧接口" },
      { id: "youtube", title: "YouTube", keywords: "YouTube cookies.txt 浏览器 cookies 代理" },
      { id: "twitter", title: "X", keywords: "X Twitter 推特 登录 长文" },
      { id: "xiaoyuzhou", title: "小宇宙", keywords: "小宇宙 播客 单集 m4a" },
      { id: "xiaohongshu", title: "小红书", keywords: "小红书 图文笔记 图片获取顺序 登录 浏览器采集" },
      { id: "zhihu", title: "知乎", keywords: "知乎 想法 回答 浏览器兜底 后台最小化" },
    ],
  },
  {
    id: "storage",
    label: "存储与网络",
    description: "资料库放在哪里、媒体保留多少、走什么网络，以及同时下载几个。",
    sections: [
      { id: "library", title: "资料库位置", keywords: "资料库位置 数据根目录 路径 媒体保留 清理" },
      { id: "network", title: "网络", keywords: "网络 代理 proxy 系统代理 无代理 自定义 SOCKS" },
      { id: "queue", title: "队列", keywords: "队列 并行下载数 VLM 并发 VLM 超时" },
    ],
  },
  {
    id: "advanced",
    label: "高级",
    description: "yt-dlp、网页抓取、远程访问和日志。",
    sections: [
      { id: "ytdlp", title: "yt-dlp", keywords: "yt-dlp 自动更新 重启后端 版本" },
      { id: "scraping", title: "网页抓取", keywords: "网页抓取 Defuddle Playwright Jina Reader 超时 绕过缓存 API Key" },
      { id: "access", title: "访问控制", keywords: "访问控制 API Token 令牌 远程文件系统" },
      { id: "logs", title: "日志", mobile: true, keywords: "日志 错误 后端" },
    ],
  },
]

function isGroupId(value: string | undefined): value is GroupId {
  return GROUPS.some((group) => group.id === value)
}

export function SettingsPanel() {
  const platform = usePlatform()
  const { online } = useAppAccess()
  const [settings, setSettings] = useState<Settings | null>(null)
  const [saving, setSaving] = useState<Record<string, boolean>>({})
  const [saved, setSaved] = useState<Record<string, boolean>>({})
  const [saveErrors, setSaveErrors] = useState<Record<string, string>>({})
  const [loadError, setLoadError] = useState<string | null>(null)
  const [apiTokenInput, setApiTokenInput] = useState("")
  const [authenticating, setAuthenticating] = useState(false)
  const [themePreference, setThemePreferenceState] = useState<ThemePreference>(getThemePreference)
  const [darkMode, setDarkMode] = useState(() => document.documentElement.classList.contains("dark"))
  const { prefs, update: updatePrefs } = usePreferences()
  const [notificationPermission, setNotificationPermission] = useState<NotificationPermission | "unsupported">(
    () => (systemNotificationsAvailable() ? Notification.permission : "unsupported"),
  )
  const route = useRoute()
  // The group lives in the link, so it can be opened directly and survives a reload.
  const activeGroupId: GroupId = isGroupId(route.params.group) ? route.params.group : "general"
  const [activeSection, setActiveSection] = useState<string | null>(null)
  const [query, setQuery] = useState("")
  const [pendingJump, setPendingJump] = useState<string | null>(null)
  const [flashSection, setFlashSection] = useState<string | null>(null)
  const [narrow, setNarrow] = useState(() => (
    typeof window.matchMedia === "function" && window.matchMedia("(max-width: 767px)").matches
  ))
  const scrollRef = useRef<HTMLDivElement>(null)
  const stickyRef = useRef<HTMLDivElement>(null)
  const openedFromList = useRef(false)
  const [uvrDetecting, setUvrDetecting] = useState(false)
  const [uvrDetection, setUvrDetection] = useState<string | null>(null)
  const [ytdlpStatus, setYtdlpStatus] = useState<YtdlpStatus | null>(null)
  const [ytdlpUpdating, setYtdlpUpdating] = useState(false)
  const [ytdlpMessage, setYtdlpMessage] = useState<string | null>(null)

  // Bilibili auth status (needed for sidebar dot indicator)
  const [biliStatus, setBiliStatus] = useState<BilibiliAuthStatus | null>(null)
  const [twitterStatus, setTwitterStatus] = useState<TwitterAuthStatus | null>(null)
  const [xiaohongshuStatus, setXiaohongshuStatus] = useState<XiaohongshuAuthStatus | null>(null)
  const biliLoggedIn = biliStatus ? biliStatus.logged_in : null

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return
    const query = window.matchMedia("(max-width: 767px)")
    const update = () => setNarrow(query.matches)
    query.addEventListener("change", update)
    return () => query.removeEventListener("change", update)
  }, [])

  const loadSettings = useCallback(async () => {
    if (!online) return false
    try {
      const loaded = await api.settings.get()
      setSettings(loaded)
      setLoadError(null)
      return true
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error))
      return false
    }
  }, [online])

  const loadYtdlpStatus = useCallback(async () => {
    if (!online) return
    try {
      const status = await api.settings.ytdlpStatus()
      setYtdlpStatus(status)
    } catch (error) {
      setYtdlpMessage(error instanceof Error ? error.message : String(error))
    }
  }, [online])

  useEffect(() => {
    if (!online) return
    void loadSettings()
    void loadYtdlpStatus()
    api.bilibili.status()
      .then(setBiliStatus)
      .catch(() => setBiliStatus({ logged_in: false, message: "无法连接后端" }))
  }, [online, loadSettings, loadYtdlpStatus])

  const unlockSettings = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setAuthenticating(true)
    try {
      await api.auth.unlock(apiTokenInput)
      await loadSettings()
    } finally {
      setAuthenticating(false)
    }
  }

  const updateSettings = useCallback(
    async (updates: Record<string, unknown>) => {
      const keys = Object.keys(updates)
      setSaving((s) => keys.reduce((acc, key) => ({ ...acc, [key]: true }), s))
      try {
        const updated = await api.settings.patch(updates)
        setSettings(updated)
        if ("ytdlp_auto_update" in updates) {
          setYtdlpStatus((status) => (
            status ? { ...status, auto_update: Boolean(updates.ytdlp_auto_update) } : status
          ))
        }
        setSaveErrors((errors) => {
          const next = { ...errors }
          for (const key of keys) delete next[key]
          return next
        })
        setSaved((s) => keys.reduce((acc, key) => ({ ...acc, [key]: true }), s))
        setTimeout(() => {
          setSaved((s) => keys.reduce((acc, key) => ({ ...acc, [key]: false }), s))
        }, 1500)
      } catch (e) {
        const message = e instanceof Error ? e.message : String(e)
        // Fields show this next to themselves; the toast covers switches and selects.
        setSaveErrors((errors) => keys.reduce((acc, key) => ({ ...acc, [key]: message }), errors))
        notifyError("设置没保存上", e)
      } finally {
        setSaving((s) => keys.reduce((acc, key) => ({ ...acc, [key]: false }), s))
      }
    },
    [],
  )

  const updateSetting = useCallback(
    (key: string, value: unknown) => updateSettings({ [key]: value }),
    [updateSettings],
  )

  // The library dialog shows its own errors, so this one lets them through.
  const switchLibrary = useCallback(async (path: string) => {
    setSettings(await api.settings.patch({ data_root: path }))
  }, [])

  useEffect(() => {
    const handleThemeChange = (event: Event) => {
      const detail = (event as CustomEvent<{ preference: ThemePreference; dark: boolean }>).detail
      setThemePreferenceState(detail.preference)
      setDarkMode(detail.dark)
    }
    window.addEventListener("mpp:theme-change", handleThemeChange)
    return () => window.removeEventListener("mpp:theme-change", handleThemeChange)
  }, [])

  // Scroll so the section starts right under the sticky group header.
  const jumpTo = useCallback((id: string, behavior: ScrollBehavior = "smooth") => {
    const container = scrollRef.current
    const element = document.getElementById(`settings-${id}`)
    if (!container || !element) return
    setActiveSection(id)
    const offset = element.getBoundingClientRect().top - container.getBoundingClientRect().top
    const top = container.scrollTop + offset - (stickyRef.current?.offsetHeight ?? 0) - 8
    if (typeof container.scrollTo === "function") container.scrollTo({ top, behavior })
    else container.scrollTop = top
  }, [])

  // A search result opens its group; scroll once the group's sections are on the page.
  useEffect(() => {
    if (!pendingJump) return
    const target = pendingJump
    const frame = requestAnimationFrame(() => {
      jumpTo(target, "auto")
      setFlashSection(target)
      setPendingJump(null)
    })
    return () => cancelAnimationFrame(frame)
  })

  useEffect(() => {
    if (!flashSection) return
    // Sections above it may still be loading (account status, platform options) and grow.
    const settle = window.setTimeout(() => jumpTo(flashSection, "auto"), 800)
    const timer = window.setTimeout(() => setFlashSection(null), 1600)
    return () => {
      window.clearTimeout(settle)
      window.clearTimeout(timer)
    }
  }, [flashSection, jumpTo])

  const updateTheme = (value: string) => {
    setThemePreference(value as ThemePreference)
  }

  if (platform.isNative && !online) {
    return (
      <div className="h-full min-h-0 max-w-[800px] space-y-4 overflow-y-auto pr-1">
        <NativeConnectionSettings />
        <OfflineSyncStatus />
        <Card>
          <CardHeader className="pb-3"><CardTitle className="text-base">外观</CardTitle></CardHeader>
          <CardContent>
            <RadioGroup value={themePreference} onValueChange={updateTheme} className="grid grid-cols-3 gap-2">
              <Label className="flex min-h-11 items-center gap-2 rounded-md border px-3"><RadioGroupItem value="system" />跟随系统</Label>
              <Label className="flex min-h-11 items-center gap-2 rounded-md border px-3"><RadioGroupItem value="light" />浅色</Label>
              <Label className="flex min-h-11 items-center gap-2 rounded-md border px-3"><RadioGroupItem value="dark" />深色</Label>
            </RadioGroup>
          </CardContent>
        </Card>
      </div>
    )
  }

  if (!settings) {
    return (
      <div className="flex min-h-[320px] items-center justify-center px-4">
        <Card className="w-full max-w-md">
          <CardHeader className="pb-3">
            <CardTitle className="text-base">API Token</CardTitle>
          </CardHeader>
          <CardContent>
            <form className="space-y-3" onSubmit={unlockSettings}>
              {loadError && (
                <p className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                  {loadError}
                </p>
              )}
              <div className="space-y-2">
                <Label htmlFor="settings-api-token">访问令牌</Label>
                <Input
                  id="settings-api-token"
                  type="password"
                  value={apiTokenInput}
                  onChange={(event) => setApiTokenInput(event.target.value)}
                  autoComplete="current-password"
                  placeholder="输入服务器 API Token"
                />
              </div>
              <Button type="submit" className="w-full" disabled={authenticating}>
                {authenticating ? "验证中..." : "进入设置"}
              </Button>
            </form>
          </CardContent>
        </Card>
      </div>
    )
  }

  const detectLocalUvr = async () => {
    setUvrDetecting(true)
    try {
      const result = await api.settings.detectLocalUvr()
      if (result.found && result.path) {
        await updateSetting("uvr_model_dir", result.path)
        if (result.models.length > 0 && !result.models.includes(String(settings?.uvr_model ?? ""))) {
          await updateSetting("uvr_model", result.models[0])
        }
        setUvrDetection(`已找到：${result.path}`)
      } else {
        setUvrDetection("未找到本机 UVR 模型目录")
      }
    } catch (e) {
      setUvrDetection(e instanceof Error ? e.message : String(e))
    } finally {
      setUvrDetecting(false)
    }
  }

  const reloadAfterBackendRestart = () => {
    window.setTimeout(() => {
      let attempts = 0
      const timer = window.setInterval(() => {
        attempts += 1
        fetch("/health", { cache: "no-store" })
          .then((response) => {
            if (!response.ok) return
            window.clearInterval(timer)
            window.location.reload()
          })
          .catch(() => {})
        if (attempts >= 30) window.clearInterval(timer)
      }, 1000)
    }, 2500)
  }

  const upgradeYtdlp = async () => {
    setYtdlpUpdating(true)
    setYtdlpMessage(null)
    try {
      const result = await api.settings.upgradeYtdlp()
      if (!result.ok) {
        setYtdlpMessage(result.output || "yt-dlp 更新失败")
        return
      }
      const restartingNow = Boolean(result.restart_scheduled) && !result.restart_after_tasks
      const after = !result.restart_scheduled
        ? "，当前环境已更新"
        : restartingNow
          ? "，后端正在重启"
          : "。还有任务在处理，后端会在它们结束后自动重启"
      setYtdlpMessage(`yt-dlp ${result.old ?? "unknown"} -> ${result.new ?? "unknown"}${after}`)
      setYtdlpStatus((status) => (
        status ? { ...status, installed: result.new, latest: result.new, is_stale: false, check_error: null } : status
      ))
      // A restart that waits for tasks may be hours away; reloading now would only drop this message.
      if (restartingNow) reloadAfterBackendRestart()
    } catch (error) {
      setYtdlpMessage(error instanceof Error ? error.message : String(error))
    } finally {
      setYtdlpUpdating(false)
    }
  }

  const visibleLlmProvider = ["local", "deepseek", "custom", "anthropic", "openai"].includes(settings.llm_provider)
    ? settings.llm_provider
    : "deepseek"

  const localModelProps = {
    settings,
    updateSetting,
    saving,
    saved,
    detectLocalUvr,
    uvrDetecting,
    uvrDetection,
  }
  const sourceProps = { settings, updateSetting, saving, saved }

  const sectionContent = (id: string): ReactNode => {
    switch (id) {
      case "appearance":
        return (
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">外观</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  {darkMode
                    ? <HugeiconsIcon icon={Moon02Icon} className="h-4 w-4" />
                    : <HugeiconsIcon icon={Sun01Icon} className="h-4 w-4" />}
                  <Label htmlFor="theme-preference">主题</Label>
                </div>
                <Select value={themePreference} onValueChange={updateTheme}>
                  <SelectTrigger id="theme-preference" className="h-11 w-32 md:h-9">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="system">跟随系统</SelectItem>
                    <SelectItem value="light">浅色</SelectItem>
                    <SelectItem value="dark">深色</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </CardContent>
          </Card>
        )
      case "startup":
        return (
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">启动页面</CardTitle>
            </CardHeader>
            <CardContent>
              <RadioGroup
                value={prefs.startupPage}
                onValueChange={(v) => updatePrefs({ startupPage: v as "files" | "last" })}
                className="flex gap-4"
              >
                <div className="flex items-center gap-2">
                  <RadioGroupItem value="files" id="startup-files" />
                  <Label htmlFor="startup-files">文件列表</Label>
                </div>
                <div className="flex items-center gap-2">
                  <RadioGroupItem value="last" id="startup-last" />
                  <Label htmlFor="startup-last">上次打开的归档</Label>
                </div>
              </RadioGroup>
            </CardContent>
          </Card>
        )
      case "playback":
        return (
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">播放</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex items-center justify-between">
                <div>
                  <Label>循环播放</Label>
                  <p className="mt-0.5 text-xs text-muted-foreground">详情页视频和音频播放器默认循环。</p>
                </div>
                <Switch
                  checked={Boolean(prefs.videoLoop)}
                  onCheckedChange={(v) => updatePrefs({ videoLoop: v })}
                />
              </div>
            </CardContent>
          </Card>
        )
      case "intake":
        return (
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">新建处理</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex items-center justify-between gap-4">
                <div>
                  <Label htmlFor="clipboard-detect">识别剪贴板里的链接</Label>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    切回 MPP 时，如果剪贴板里有还没处理过的新链接，提示一键新建，同一个链接只提示一次。浏览器会先询问一次剪贴板权限。
                    在文件库里直接 Ctrl+V 粘贴链接、把音视频拖进窗口也能新建；桌面版还可以按 Ctrl+N。
                  </p>
                </div>
                <Switch
                  id="clipboard-detect"
                  checked={prefs.clipboardDetect !== false}
                  onCheckedChange={(v) => updatePrefs({ clipboardDetect: v })}
                />
              </div>
              <div className="mt-4 flex items-center justify-between gap-4 border-t pt-4">
                <div>
                  <Label htmlFor="task-notifications">完成或失败时通知</Label>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    在 MPP 里弹出提示，点「打开」看结果；MPP 不在前台时发系统通知。
                    {notificationPermission === "denied" && " 系统通知已被浏览器禁止，需要在浏览器的网站设置里允许。"}
                  </p>
                </div>
                <Switch
                  id="task-notifications"
                  checked={prefs.taskNotifications !== false}
                  onCheckedChange={(v) => {
                    updatePrefs({ taskNotifications: v })
                    // Browsers ask once, and only in response to a click.
                    if (v && notificationPermission === "default") {
                      void Notification.requestPermission().then(setNotificationPermission)
                    }
                  }}
                />
              </div>
            </CardContent>
          </Card>
        )
      case "audio-flow":
        return <LocalModelSection id="audio-flow" {...localModelProps} />
      case "uvr":
        return <LocalModelSection id="uvr" {...localModelProps} />
      case "asr":
        return <LocalModelSection id="sherpa-asr" {...localModelProps} />
      case "models":
        return <PurposeModelBindings settings={settings} updateSetting={updateSetting} />
      case "local-llm":
        return <LocalModelSection id="local-llm" {...localModelProps} />
      case "inference":
        return (
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">推理模式</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex items-center justify-between">
                <div>
                  <Label>半重叠推理</Label>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    开启：下载与 GPU 步骤并行（更快）。关闭：串行执行（显存更省）。
                    建议 32 GB+ 显存开启，16 GB 及以下关闭。
                  </p>
                </div>
                <Switch
                  checked={Boolean(settings.pipeline_overlap ?? true)}
                  onCheckedChange={(v) => updateSetting("pipeline_overlap", v)}
                />
              </div>
              <Separator />
              <div className="flex items-center justify-between">
                <div>
                  <Label>生成视频详情 detail.md</Label>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    开启后额外生成旧版深层树状 Markdown，作为“视频详情”展示和导出；默认导图保持浅层展示型结构。
                  </p>
                </div>
                <Switch
                  checked={Boolean(settings.generate_video_detail ?? true)}
                  onCheckedChange={(v) => updateSetting("generate_video_detail", v)}
                />
              </div>
            </CardContent>
          </Card>
        )
      case "knowledge":
        return (
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">知识库索引</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-xs text-muted-foreground">OpenAI 兼容的嵌入 API，用于任务完成后自动索引字幕和摘要。</p>
              <div className="flex items-center justify-between gap-4">
                <Label htmlFor="kb-enabled">自动索引</Label>
                <Switch
                  id="kb-enabled"
                  checked={Boolean(settings.kb_enabled ?? true)}
                  onCheckedChange={(value) => updateSetting("kb_enabled", value)}
                />
              </div>
              <SettingRow
                label="API 地址"
                settingKey="kb_embedding_api_base"
                value={String(settings.kb_embedding_api_base ?? "")}
                onSave={updateSetting}
                saving={saving}
                saved={saved}
                placeholder="http://localhost:8080/v1"
              />
              <SettingRow
                label="API Key"
                settingKey="kb_embedding_api_key"
                value={String(settings.kb_embedding_api_key ?? "")}
                onSave={updateSetting}
                saving={saving}
                saved={saved}
                masked
              />
              <SettingRow
                label="模型"
                settingKey="kb_embedding_model"
                value={String(settings.kb_embedding_model ?? "qwen3-embedding-0.6b")}
                onSave={updateSetting}
                saving={saving}
                saved={saved}
              />
              <SettingRow
                label="向量维度"
                settingKey="kb_embedding_dim"
                value={String(settings.kb_embedding_dim ?? 1024)}
                onSave={updateSetting}
                saving={saving}
                saved={saved}
                short
              />
            </CardContent>
          </Card>
        )
      case "bilibili":
        return <BilibiliCard {...sourceProps} onAuthChange={setBiliStatus} />
      case "youtube":
        return <YoutubeCard {...sourceProps} />
      case "twitter":
        return <TwitterCard {...sourceProps} onStatusChange={setTwitterStatus} />
      case "xiaoyuzhou":
        return (
          <PlaceholderSection
            title="小宇宙"
            description="已支持公开单集页面：提取页面元数据、下载 m4a，并转为本地 ASR 使用的 wav。"
            comingSoon={false}
          />
        )
      case "xiaohongshu":
        return <XiaohongshuCard {...sourceProps} onStatusChange={setXiaohongshuStatus} />
      case "zhihu":
        return <ZhihuCard {...sourceProps} />
      case "library":
        return (
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">资料库位置</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <LibraryLocation value={String(settings.data_root ?? "")} onSwitch={switchLibrary} />
              <div className="flex items-center gap-3">
                <Label htmlFor="media-retention-policy" className="w-24 shrink-0 text-sm text-muted-foreground">媒体保留</Label>
                <OptionSelect
                  id="media-retention-policy"
                  value={String(settings.media_retention_policy ?? "all")}
                  disabled={saving.media_retention_policy}
                  onValueChange={(value) => void updateSetting("media_retention_policy", value)}
                >
                  {Object.entries(mediaPolicies).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </OptionSelect>
              </div>
              <p className="text-xs text-muted-foreground">用于下次媒体清理预览。文件页右键归档可预览并执行。</p>
            </CardContent>
          </Card>
        )
      case "network":
        return (
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">网络</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <ProxySetting
                label="代理"
                settingKey="network_proxy"
                value={String(settings.network_proxy ?? "")}
                onSave={updateSetting}
                saving={saving}
                saved={saved}
              />
              <p className="pl-[6.75rem] text-xs text-muted-foreground">
                {String(settings.youtube_proxy ?? "").trim()
                  ? `YouTube 单独设置了代理（${describeProxy(String(settings.youtube_proxy))}），不跟随这里；可以在「来源与账号 › YouTube」里改回跟随全局。`
                  : "YouTube 默认也用这里的代理，可以在「来源与账号 › YouTube」里单独设置。"}
              </p>
            </CardContent>
          </Card>
        )
      case "queue":
        return (
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">队列</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex items-center gap-3">
                <Label className="w-24 shrink-0 text-sm text-muted-foreground">并行下载数</Label>
                <OptionSelect
                  aria-label="并行下载数"
                  value={String(settings.max_download_concurrency ?? 2)}
                  onValueChange={(value) => updateSetting("max_download_concurrency", Number(value))}
                >
                  {[1, 2, 3, 4].map((n) => (
                    <option key={n} value={n}>{n}</option>
                  ))}
                </OptionSelect>
                {saved.max_download_concurrency && (
                  <HugeiconsIcon icon={Tick02Icon} className="h-3.5 w-3.5 text-emerald-500" />
                )}
              </div>
              <div className="flex items-center gap-3">
                <Label className="w-24 shrink-0 text-sm text-muted-foreground">VLM 并发</Label>
                <OptionSelect
                  aria-label="VLM 并发"
                  value={String(settings.vlm_concurrency ?? 1)}
                  onValueChange={(value) => updateSetting("vlm_concurrency", Number(value))}
                >
                  {[1, 2, 3, 4].map((n) => (
                    <option key={n} value={n}>{n}</option>
                  ))}
                </OptionSelect>
                {saved.vlm_concurrency && (
                  <HugeiconsIcon icon={Tick02Icon} className="h-3.5 w-3.5 text-emerald-500" />
                )}
              </div>
              <SettingRow
                label="VLM 超时"
                settingKey="vlm_timeout_sec"
                value={String(settings.vlm_timeout_sec ?? 180)}
                onSave={(key, value) => updateSetting(key, Math.max(30, Number(value) || 180))}
                saving={saving}
                saved={saved}
                placeholder="180"
                unit="秒"
              />
            </CardContent>
          </Card>
        )
      case "ytdlp":
        return (
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">yt-dlp</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex items-center justify-between">
                <div>
                  <Label>启动时自动更新</Label>
                  <p className="mt-0.5 text-xs text-muted-foreground">每次启动按清华源 → 中科大源 → 系统默认源检查，有新稳定版本时更新。</p>
                </div>
                <Switch
                  checked={Boolean(settings.ytdlp_auto_update ?? true)}
                  onCheckedChange={(v) => updateSetting("ytdlp_auto_update", v)}
                />
              </div>
              <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
                <span>当前 {ytdlpStatus?.installed ?? "unknown"}</span>
                <span>最新 {ytdlpStatus?.latest ?? "unknown"}</span>
                {ytdlpStatus?.is_stale && <span className="text-amber-600">可更新</span>}
                {saved.ytdlp_auto_update && (
                  <HugeiconsIcon icon={Tick02Icon} className="h-3.5 w-3.5 text-emerald-500" />
                )}
              </div>
              <div className="space-y-1">
                <Button type="button" variant="outline" onClick={upgradeYtdlp} disabled={ytdlpUpdating}>
                  {ytdlpUpdating && <HugeiconsIcon icon={Loading03Icon} className="mr-2 h-4 w-4 animate-spin" />}
                  更新到最新
                </Button>
                <p className="text-xs text-muted-foreground">更新后要重启后端才生效；有任务在处理时，会等它们结束再自动重启。</p>
              </div>
              {ytdlpStatus?.source && <p className="text-xs text-muted-foreground">检查来源：{ytdlpStatus.source}</p>}
              {ytdlpStatus?.check_error && <p className="text-xs text-muted-foreground">{ytdlpStatus.check_error}</p>}
              {ytdlpMessage && <p className="text-xs text-muted-foreground">{ytdlpMessage}</p>}
            </CardContent>
          </Card>
        )
      case "scraping":
        return (
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">网页抓取</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-xs leading-5 text-muted-foreground">
                网页抓取的兜底服务。通用网页先使用本地 Defuddle，失败后调用 Jina Reader 返回 Markdown。
              </p>
              <div className="flex items-center justify-between">
                <div>
                  <Label>启用 Defuddle</Label>
                  <p className="mt-0.5 text-xs text-muted-foreground">本地抽取通用网页正文与元数据。</p>
                </div>
                <Switch
                  checked={Boolean(settings.defuddle_enabled ?? true)}
                  onCheckedChange={(v) => updateSetting("defuddle_enabled", v)}
                />
              </div>
              <div className="flex items-center justify-between">
                <div>
                  <Label>启用 Playwright</Label>
                  <p className="mt-0.5 text-xs text-muted-foreground">处理需要浏览器渲染的 X 等动态页面。</p>
                </div>
                <Switch
                  checked={Boolean(settings.playwright_enabled ?? true)}
                  onCheckedChange={(v) => updateSetting("playwright_enabled", v)}
                />
              </div>
              <div className="flex items-center justify-between">
                <div>
                  <Label>启用 Jina Reader</Label>
                  <p className="mt-0.5 text-xs text-muted-foreground">用于 Defuddle 抽取失败后的网页正文解析。</p>
                </div>
                <Switch
                  checked={Boolean(settings.jina_reader_enabled ?? true)}
                  onCheckedChange={(v) => updateSetting("jina_reader_enabled", v)}
                />
              </div>
              <SettingRow
                label="API 地址"
                settingKey="jina_reader_api_base"
                value={String(settings.jina_reader_api_base ?? "https://r.jina.ai")}
                onSave={updateSetting}
                saving={saving}
                saved={saved}
                placeholder="https://r.jina.ai"
              />
              <SettingRow
                label="API Key"
                settingKey="jina_reader_api_key"
                value={String(settings.jina_reader_api_key ?? "")}
                onSave={updateSetting}
                saving={saving}
                saved={saved}
                masked
                placeholder="可选 Bearer Token"
              />
              <SettingRow
                label="超时秒数"
                settingKey="web_scrape_timeout_sec"
                value={String(settings.web_scrape_timeout_sec ?? 30)}
                onSave={(key, value) => updateSetting(key, Number(value) || 30)}
                saving={saving}
                saved={saved}
                placeholder="30"
                unit="秒"
              />
              <div className="flex items-center justify-between">
                <div>
                  <Label>绕过缓存</Label>
                  <p className="mt-0.5 text-xs text-muted-foreground">需要实时刷新网页时打开。</p>
                </div>
                <Switch
                  checked={Boolean(settings.jina_reader_bypass_cache ?? false)}
                  onCheckedChange={(v) => updateSetting("jina_reader_bypass_cache", v)}
                />
              </div>
            </CardContent>
          </Card>
        )
      case "access":
        return (
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">访问控制</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <SettingRow
                label="API Token"
                settingKey="api_token"
                value={String(settings.api_token ?? "")}
                onSave={updateSetting}
                saving={saving}
                saved={saved}
                masked
                placeholder="留空则不启用"
              />
              <div className="flex items-center justify-between gap-4">
                <div>
                  <Label>远程文件系统</Label>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    允许远程浏览服务器目录、提交本地路径并打开系统文件夹。
                  </p>
                </div>
                <Switch
                  checked={Boolean(settings.allow_remote_filesystem)}
                  onCheckedChange={(value) => updateSetting("allow_remote_filesystem", value)}
                />
              </div>
            </CardContent>
          </Card>
        )
      case "logs":
        return (
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">日志</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex items-center justify-between gap-4">
                <p className="text-sm text-muted-foreground">后端的完整日志和最近的错误在「后端」页查看。</p>
                <Button type="button" variant="outline" size="sm" onClick={() => navigate("#/backend?tab=logs")}>
                  打开日志
                </Button>
              </div>
            </CardContent>
          </Card>
        )
      default:
        return null
    }
  }

  // Phones and the Android app change only what makes sense there; the rest they can read.
  const onlyMobile = platform.isNative || narrow
  const readOnly = (section: SectionDef) => onlyMobile && !section.mobile
  const groups = GROUPS
  const activeGroup = groups.find((group) => group.id === activeGroupId) ?? groups[0]
  const sections = activeGroup.sections
  const needle = query.trim().toLowerCase()
  // A phone opens settings on the list of groups, and a group on a page of its own.
  const phoneList = narrow && !isGroupId(route.params.group) && !needle

  const selectGroup = (id: GroupId) => {
    setActiveSection(null)
    if (scrollRef.current) scrollRef.current.scrollTop = 0
    if (narrow) {
      // A new history entry, so going back returns to the list.
      openedFromList.current = !isGroupId(route.params.group)
      navigate(buildHash("settings", { group: id }))
      return
    }
    navigate(buildHash("settings", { group: id === "general" ? null : id }), { replace: true })
  }

  const backToList = () => {
    if (openedFromList.current) {
      openedFromList.current = false
      window.history.back()
      return
    }
    navigate(buildHash("settings", {}), { replace: true })
  }

  const results = needle
    ? groups.flatMap((group) => group.sections
      .filter((section) => `${group.label} ${section.title} ${section.keywords ?? ""}`.toLowerCase().includes(needle))
      .map((section) => ({ group, section })))
    : []

  const openResult = (groupId: GroupId, sectionId: string) => {
    setQuery("")
    if (groupId !== activeGroup.id) selectGroup(groupId)
    setPendingJump(sectionId)
  }

  // The section at the top of the view is the one marked in the section links.
  const followScroll = () => {
    const container = scrollRef.current
    if (!container) return
    // A section counts once its top has reached the bottom of the sticky group header.
    const reached = container.getBoundingClientRect().top + (stickyRef.current?.offsetHeight ?? 0) + 24
    const passed = sections.filter((section) => {
      const element = document.getElementById(`settings-${section.id}`)
      return element && element.getBoundingClientRect().top <= reached
    })
    setActiveSection(passed.at(-1)?.id ?? sections[0]?.id ?? null)
  }

  const searchBox = (id: string) => (
    <div className="relative">
      <HugeiconsIcon icon={Search01Icon} className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
      <Input
        id={id}
        type="search"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape") setQuery("")
          if (event.key === "Enter" && results[0]) openResult(results[0].group.id, results[0].section.id)
        }}
        placeholder="搜索设置"
        aria-label="搜索设置"
        className="h-8 pl-8 text-sm"
      />
    </div>
  )

  return (
    <SettingsSaveErrors.Provider value={saveErrors}>
    <div className="h-full min-h-0 w-full">
      <div className="flex h-full min-h-0 flex-col gap-3 lg:flex-row lg:gap-5">
        {narrow ? (
          <div className="shrink-0">
            {isGroupId(route.params.group) && !needle ? (
              <Button type="button" variant="ghost" onClick={backToList} className="-ml-2 h-11 gap-1 px-2">
                <HugeiconsIcon icon={ArrowLeft01Icon} className="size-4" />
                全部设置
              </Button>
            ) : (
              searchBox("settings-search-mobile")
            )}
            {phoneList && (
              <ul aria-label="设置分组" className="mt-3 divide-y rounded-lg border">
                {groups.map((group) => (
                  <li key={group.id}>
                    <button
                      type="button"
                      onClick={() => selectGroup(group.id)}
                      className="flex min-h-14 w-full items-center gap-3 px-3 py-2.5 text-left hover:bg-muted"
                    >
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm font-medium">{group.label}</span>
                        <span className="block truncate text-xs text-muted-foreground">{group.description}</span>
                      </span>
                      {group.id === "sources" && biliLoggedIn !== null && (
                        <span
                          className={["size-1.5 shrink-0 rounded-full", biliLoggedIn ? "bg-emerald-500" : "bg-red-500"].join(" ")}
                          aria-label={biliLoggedIn ? "哔哩哔哩已登录" : "哔哩哔哩未登录"}
                        />
                      )}
                      <HugeiconsIcon icon={ArrowRight01Icon} className="size-4 shrink-0 text-muted-foreground" />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        ) : (
          <div className="shrink-0 lg:hidden">
            {searchBox("settings-search-mobile")}
            <Label htmlFor="settings-category" className="mb-1.5 mt-3 block text-xs text-muted-foreground">
              设置分组
            </Label>
            <Select value={activeGroup.id} onValueChange={(value) => selectGroup(value as GroupId)}>
              <SelectTrigger id="settings-category" className="h-11! w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {groups.map((group) => (
                  <SelectItem key={group.id} value={group.id} className="min-h-11">
                    {group.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}

        <nav
          aria-label="设置分组"
          className="hidden w-full shrink-0 rounded-lg border bg-card p-1 lg:sticky lg:top-5 lg:block lg:h-fit lg:w-[220px] lg:space-y-1 lg:p-2"
        >
          <div className="pb-1">{searchBox("settings-search")}</div>
          {groups.map((group) => {
            const isActive = activeGroup.id === group.id
            return (
              <button
                key={group.id}
                type="button"
                onClick={() => selectGroup(group.id)}
                aria-current={isActive ? "page" : undefined}
                className={[
                  "inline-flex min-w-max items-center gap-2 rounded-md px-3 py-2 text-left text-sm transition-colors lg:flex lg:w-full",
                  isActive
                    ? "bg-primary/10 text-primary font-medium"
                    : "text-muted-foreground hover:text-foreground hover:bg-accent/50",
                ].join(" ")}
              >
                <span className="truncate flex-1">{group.label}</span>
                {group.id === "sources" && biliLoggedIn !== null && (
                  <span
                    className={[
                      "h-1.5 w-1.5 rounded-full shrink-0",
                      biliLoggedIn ? "bg-emerald-500" : "bg-red-500",
                    ].join(" ")}
                    aria-label={biliLoggedIn ? "哔哩哔哩已登录" : "哔哩哔哩未登录"}
                  />
                )}
              </button>
            )
          })}
        </nav>

        <div hidden={phoneList} className="min-h-0 min-w-0 flex-1 overflow-hidden [&_[data-slot=card]]:rounded-none [&_[data-slot=card]]:bg-transparent [&_[data-slot=card]]:py-0 [&_[data-slot=card]]:ring-0 [&_[data-slot=card]]:border-b [&_[data-slot=card]]:border-border/70 [&_[data-slot=card]]:pb-4 [&_[data-slot=card-header]]:px-0 [&_[data-slot=card-header]]:pb-1.5 [&_[data-slot=card-content]]:px-0">
          {needle ? (
            <div className="h-full min-h-0 max-w-[800px] overflow-y-auto pr-1">
              <h2 className="text-lg font-semibold">搜索设置</h2>
              <p className="mt-0.5 text-sm text-muted-foreground">
                {results.length ? `「${query.trim()}」出现在 ${results.length} 处` : `没有和「${query.trim()}」有关的设置`}
              </p>
              <ul className="mt-3 divide-y rounded-lg border">
                {results.map(({ group, section }) => (
                  <li key={`${group.id}-${section.id}`}>
                    <button
                      type="button"
                      onClick={() => openResult(group.id, section.id)}
                      className="flex w-full items-baseline gap-3 px-3 py-2.5 text-left hover:bg-muted"
                    >
                      <span className="text-sm font-medium">{section.title}</span>
                      <span className="text-xs text-muted-foreground">{group.label}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ) : activeGroup.id === "providers" && onlyMobile ? (
            <div className="h-full min-h-0 overflow-y-auto">
              <h2 className="text-lg font-semibold">{activeGroup.label}</h2>
              <p className="mb-3 mt-0.5 text-sm text-muted-foreground">在手机上只能查看，添加和修改服务商请用电脑打开设置。</p>
              <ProviderSummary settings={settings} />
            </div>
          ) : activeGroup.id === "providers" ? (
            <RegistrySettings
              settings={settings}
              visibleLlmProvider={visibleLlmProvider}
              updateSetting={updateSetting}
              updateSettings={updateSettings}
              saving={saving}
              saved={saved}
            />
          ) : (
            <div
              ref={scrollRef}
              onScroll={followScroll}
              className="h-full min-h-0 max-w-[800px] overflow-y-auto pr-1"
            >
              <div ref={stickyRef} className="sticky top-0 z-10 bg-background pb-3">
                <h2 className="text-lg font-semibold">{activeGroup.label}</h2>
                <p className="mt-0.5 text-sm text-muted-foreground">{activeGroup.description}</p>
                {sections.some(readOnly) && (
                  <p className="mt-1 text-xs text-muted-foreground">
                    {sections.every(readOnly) ? "这一组" : "灰色的部分"}在手机上只能查看，修改请用电脑打开设置。
                  </p>
                )}
                {sections.length > 1 && (
                  <nav aria-label={`${activeGroup.label}的分区`} className="mt-3 flex flex-wrap gap-1">
                    {sections.map((section) => (
                      <button
                        key={section.id}
                        type="button"
                        onClick={() => jumpTo(section.id)}
                        aria-current={(activeSection ?? sections[0].id) === section.id ? "location" : undefined}
                        className={[
                          "rounded-md px-2.5 py-1 text-xs transition-colors",
                          (activeSection ?? sections[0].id) === section.id
                            ? "bg-primary/10 font-medium text-primary"
                            : "text-muted-foreground hover:bg-muted hover:text-foreground",
                        ].join(" ")}
                      >
                        {section.title}
                      </button>
                    ))}
                  </nav>
                )}
              </div>
              <div className="space-y-4 pb-8">
                {activeGroup.id === "general" && (
                  <>
                    <NativeConnectionSettings />
                    <OfflineSyncStatus />
                  </>
                )}
                {activeGroup.id === "sources" && (
                  <SourceOverview
                    settings={settings}
                    bilibili={biliStatus}
                    twitter={twitterStatus}
                    xiaohongshu={xiaohongshuStatus}
                    onOpen={(sectionId) => setPendingJump(sectionId)}
                  />
                )}
                {sections.map((section) => (
                  <section
                    key={section.id}
                    id={`settings-${section.id}`}
                    aria-label={section.title}
                    className={flashSection === section.id ? "rounded-md ring-2 ring-primary/40 ring-offset-4 ring-offset-background transition-shadow" : undefined}
                  >
                    {readOnly(section) ? (
                      // fieldset disables every control inside; pointer-events keeps Radix triggers shut too
                      <fieldset disabled className="m-0 min-w-0 border-0 p-0 [&_button]:pointer-events-none [&_input]:pointer-events-none">
                        {sectionContent(section.id)}
                      </fieldset>
                    ) : sectionContent(section.id)}
                  </section>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
    </SettingsSaveErrors.Provider>
  )
}
