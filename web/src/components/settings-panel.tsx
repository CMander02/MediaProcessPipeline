import { useEffect, useState, useCallback } from "react"
import React from "react"
import { isNewModel } from "@/lib/model-releases"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Separator } from "@/components/ui/separator"
import { Switch } from "@/components/ui/switch"
import { Slider } from "@/components/ui/slider"
import { Badge } from "@/components/ui/badge"
import { api, type Settings } from "@/lib/api"
import { usePreferences } from "@/hooks/use-preferences"
import { HugeiconsIcon } from "@hugeicons/react"
import { FloppyDiskIcon, Tick02Icon, Moon02Icon, Sun01Icon } from "@hugeicons/core-free-icons"

function NewBadge() {
  return (
    <Badge className="rounded-sm border-transparent bg-gradient-to-r from-indigo-500 to-pink-500 [background-size:105%] bg-center text-white text-[10px] px-1.5 py-0 leading-4 shrink-0">
      NEW!
    </Badge>
  )
}

function ComingSoonBadge() {
  return (
    <Badge variant="outline" className="text-[10px] px-1.5 py-0">即将</Badge>
  )
}

function ModelLabel({ name }: { name: string }) {
  const isNew = isNewModel(name)
  return (
    <span className="flex items-center gap-1.5">
      {name}
      {isNew && <NewBadge />}
    </span>
  )
}

// --- Tab definitions ---

type TabId = "general" | "ai" | "bilibili" | "youtube" | "xiaoyuzhou" | "xiaohongshu" | "zhihu"

interface TabDef {
  id: TabId
  label: string
  comingSoon?: boolean
}

const TABS: TabDef[] = [
  { id: "general", label: "通用" },
  { id: "ai", label: "AI 模型" },
  { id: "bilibili", label: "哔哩哔哩" },
  { id: "youtube", label: "YouTube" },
  { id: "xiaoyuzhou", label: "小宇宙", comingSoon: true },
  { id: "xiaohongshu", label: "小红书", comingSoon: true },
  { id: "zhihu", label: "知乎", comingSoon: true },
]

// --- Placeholder section for coming-soon platforms ---

interface PlaceholderSectionProps {
  title: string
  description: string
}

function PlaceholderSection({ title, description }: PlaceholderSectionProps) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base flex items-center gap-2">
          {title}
          <ComingSoonBadge />
        </CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-sm text-muted-foreground">{description}</p>
      </CardContent>
    </Card>
  )
}

export function SettingsPanel() {
  const [settings, setSettings] = useState<Settings | null>(null)
  const [saving, setSaving] = useState<Record<string, boolean>>({})
  const [saved, setSaved] = useState<Record<string, boolean>>({})
  const [darkMode, setDarkMode] = useState(
    () => document.documentElement.classList.contains("dark"),
  )
  const { prefs, update: updatePrefs } = usePreferences()
  const [activeTab, setActiveTab] = useState<TabId>("general")

  // Bilibili auth status (needed for sidebar dot indicator)
  const [biliLoggedIn, setBiliLoggedIn] = useState<boolean | null>(null)

  useEffect(() => {
    api.settings.get().then(setSettings).catch(() => {})
    api.bilibili.status()
      .then((s) => setBiliLoggedIn(s.logged_in))
      .catch(() => setBiliLoggedIn(false))
  }, [])

  const updateSetting = useCallback(
    async (key: string, value: unknown) => {
      setSaving((s) => ({ ...s, [key]: true }))
      try {
        const updated = await api.settings.patch({ [key]: value })
        setSettings(updated)
        setSaved((s) => ({ ...s, [key]: true }))
        setTimeout(() => setSaved((s) => ({ ...s, [key]: false })), 1500)
      } catch {}
      setSaving((s) => ({ ...s, [key]: false }))
    },
    [],
  )

  const toggleDark = () => {
    const next = !darkMode
    setDarkMode(next)
    document.documentElement.classList.toggle("dark", next)
    localStorage.setItem("theme", next ? "dark" : "light")
  }

  if (!settings) {
    return <p className="text-sm text-muted-foreground">加载中...</p>
  }

  return (
    <div className="max-w-3xl">
      <div className="flex gap-0 min-h-[400px]">
        {/* Left sidebar */}
        <nav className="w-[140px] shrink-0 pr-3 space-y-1">
          {TABS.map((tab, idx) => {
            const isActive = activeTab === tab.id
            // Insert a separator before the coming-soon group
            const prevTab = TABS[idx - 1]
            const insertSep = prevTab && !prevTab.comingSoon && tab.comingSoon

            return (
              <React.Fragment key={tab.id}>
                {insertSep && <Separator className="my-2" />}
                <button
                  onClick={() => setActiveTab(tab.id)}
                  className={[
                    "w-full text-left flex items-center gap-1.5 px-2 py-1.5 rounded-md text-sm transition-colors",
                    isActive
                      ? "bg-accent text-accent-foreground font-medium"
                      : "text-muted-foreground hover:text-foreground hover:bg-accent/50",
                  ].join(" ")}
                >
                  <span className="truncate flex-1">{tab.label}</span>
                  {tab.id === "bilibili" && biliLoggedIn !== null && (
                    <span
                      className={[
                        "h-1.5 w-1.5 rounded-full shrink-0",
                        biliLoggedIn ? "bg-emerald-500" : "bg-red-500",
                      ].join(" ")}
                    />
                  )}
                  {tab.comingSoon && <ComingSoonBadge />}
                </button>
              </React.Fragment>
            )
          })}
        </nav>

        {/* Vertical divider */}
        <Separator orientation="vertical" className="self-stretch" />

        {/* Right content */}
        <div className="flex-1 pl-5 space-y-4 min-w-0">
          {/* ── 通用 ── */}
          {activeTab === "general" && (
            <>
              {/* Appearance */}
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
                      <Label>深色模式</Label>
                    </div>
                    <Switch checked={darkMode} onCheckedChange={toggleDark} />
                  </div>
                </CardContent>
              </Card>

              {/* Startup page */}
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

              {/* Data Paths */}
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="text-base">数据路径</CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                  <SettingRow
                    label="数据根目录"
                    settingKey="data_root"
                    value={String(settings.data_root ?? "")}
                    onSave={updateSetting}
                    saving={saving}
                    saved={saved}
                    placeholder="绝对路径，如 C:\data\mpp"
                  />
                </CardContent>
              </Card>

              {/* Security */}
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
                </CardContent>
              </Card>

              {/* Queue */}
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="text-base">队列</CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                  <div className="flex items-center gap-3">
                    <Label className="w-24 shrink-0 text-sm text-muted-foreground">并行下载数</Label>
                    <select
                      value={String(settings.max_download_concurrency ?? 2)}
                      onChange={(e) => updateSetting("max_download_concurrency", Number(e.target.value))}
                      className="h-8 rounded-md border border-input bg-background px-3 text-sm"
                    >
                      {[1, 2, 3, 4].map((n) => (
                        <option key={n} value={n}>{n}</option>
                      ))}
                    </select>
                    {saved.max_download_concurrency && (
                      <HugeiconsIcon icon={Tick02Icon} className="h-3.5 w-3.5 text-emerald-500" />
                    )}
                  </div>
                </CardContent>
              </Card>

              {/* Pipeline mode */}
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
                </CardContent>
              </Card>
            </>
          )}

          {/* ── AI 模型 ── */}
          {activeTab === "ai" && (
            <>
              {/* ASR */}
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="text-base flex items-center gap-2">
                    ASR
                    {isNewModel(String(settings?.qwen3_asr_model_path || "Qwen/Qwen3-ASR-1.7B")) && <NewBadge />}
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  <p className="text-sm text-muted-foreground">当前固定使用 Qwen3-ASR。</p>
                  <Separator />
                  <div className="space-y-3">
                    <SettingRow
                      label="模型路径"
                      settingKey="qwen3_asr_model_path"
                      value={String(settings.qwen3_asr_model_path ?? "")}
                      onSave={updateSetting}
                      saving={saving}
                      saved={saved}
                    />
                    <SettingRow
                      label="设备"
                      settingKey="qwen3_device"
                      value={String(settings.qwen3_device ?? "cuda")}
                      onSave={updateSetting}
                      saving={saving}
                      saved={saved}
                    />
                  </div>
                </CardContent>
              </Card>

              {/* LLM Provider */}
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="text-base">LLM 提供商</CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  <RadioGroup
                    value={settings.llm_provider}
                    onValueChange={(v) => updateSetting("llm_provider", v)}
                    className="flex flex-wrap gap-4"
                  >
                    <div className="flex items-center gap-2">
                      <RadioGroupItem value="anthropic" id="llm-anthropic" />
                      <Label htmlFor="llm-anthropic" className="flex items-center gap-1.5">Anthropic <NewBadge /></Label>
                    </div>
                    <div className="flex items-center gap-2">
                      <RadioGroupItem value="openai" id="llm-openai" />
                      <Label htmlFor="llm-openai">OpenAI</Label>
                    </div>
                    <div className="flex items-center gap-2">
                      <RadioGroupItem value="deepseek" id="llm-deepseek" />
                      <Label htmlFor="llm-deepseek" className="flex items-center gap-1.5">DeepSeek <NewBadge /></Label>
                    </div>
                    <div className="flex items-center gap-2">
                      <RadioGroupItem value="custom" id="llm-custom" />
                      <Label htmlFor="llm-custom">Custom</Label>
                    </div>
                    <div className="flex items-center gap-2">
                      <RadioGroupItem value="local" id="llm-local" />
                      <Label htmlFor="llm-local">本地 HF 模型</Label>
                    </div>
                  </RadioGroup>

                  <Separator />

                  {settings.llm_provider === "local" ? (
                    <div className="space-y-3">
                      <SettingRow
                        label="模型目录"
                        settingKey="local_llm_model_path"
                        value={String(settings.local_llm_model_path ?? "")}
                        onSave={updateSetting}
                        saving={saving}
                        saved={saved}
                        placeholder="包含 config.json 和 *.safetensors 的目录"
                      />
                      <div className="flex items-center gap-3">
                        <Label className="w-24 shrink-0 text-sm text-muted-foreground">设备</Label>
                        <select
                          value={String(settings.local_llm_device ?? "cuda")}
                          onChange={(e) => updateSetting("local_llm_device", e.target.value)}
                          className="h-8 rounded-md border border-input bg-background px-3 text-sm"
                        >
                          <option value="cuda">cuda</option>
                          <option value="cpu">cpu</option>
                          <option value="auto">auto</option>
                        </select>
                      </div>
                      <div className="flex items-center gap-3">
                        <Label className="w-24 shrink-0 text-sm text-muted-foreground">精度</Label>
                        <select
                          value={String(settings.local_llm_dtype ?? "bfloat16")}
                          onChange={(e) => updateSetting("local_llm_dtype", e.target.value)}
                          className="h-8 rounded-md border border-input bg-background px-3 text-sm"
                        >
                          <option value="bfloat16">bfloat16</option>
                          <option value="float16">float16</option>
                          <option value="float32">float32</option>
                          <option value="auto">auto</option>
                        </select>
                      </div>
                      <SettingRow
                        label="最大生成长度"
                        settingKey="local_llm_max_new_tokens"
                        value={String(settings.local_llm_max_new_tokens ?? 4096)}
                        onSave={(key, val) => updateSetting(key, Number(val))}
                        saving={saving}
                        saved={saved}
                      />
                    </div>
                  ) : settings.llm_provider === "custom" ? (
                    <div className="space-y-3">
                      <SettingRow
                        label="名称"
                        settingKey="custom_name"
                        value={String(settings.custom_name ?? "")}
                        onSave={updateSetting}
                        saving={saving}
                        saved={saved}
                      />
                      <SettingRow
                        label="API Base"
                        settingKey="custom_api_base"
                        value={String(settings.custom_api_base ?? "")}
                        onSave={updateSetting}
                        saving={saving}
                        saved={saved}
                      />
                      <SettingRow
                        label="模型"
                        settingKey="custom_model"
                        value={String(settings.custom_model ?? "")}
                        onSave={updateSetting}
                        saving={saving}
                        saved={saved}
                      />
                      <SettingRow
                        label="API Key"
                        settingKey="custom_api_key"
                        value={String(settings.custom_api_key ?? "")}
                        onSave={updateSetting}
                        saving={saving}
                        saved={saved}
                        masked
                      />
                    </div>
                  ) : settings.llm_provider === "deepseek" ? (
                    <DeepSeekConfig
                      settings={settings}
                      updateSetting={updateSetting}
                      saving={saving}
                      saved={saved}
                    />
                  ) : settings.llm_provider === "anthropic" ? (
                    <div className="space-y-3">
                      <SettingRow
                        label={<span className="flex items-center gap-1.5">模型{isNewModel(String(settings.anthropic_model ?? "")) && <NewBadge />}</span>}
                        settingKey="anthropic_model"
                        value={String(settings.anthropic_model ?? "")}
                        onSave={updateSetting}
                        saving={saving}
                        saved={saved}
                      />
                      <SettingRow
                        label="API Key"
                        settingKey="anthropic_api_key"
                        value={String(settings.anthropic_api_key ?? "")}
                        onSave={updateSetting}
                        saving={saving}
                        saved={saved}
                        masked
                      />
                    </div>
                  ) : (
                    <div className="space-y-3">
                      <SettingRow
                        label="模型"
                        settingKey="openai_model"
                        value={String(settings.openai_model ?? "")}
                        onSave={updateSetting}
                        saving={saving}
                        saved={saved}
                      />
                      <SettingRow
                        label="API Key"
                        settingKey="openai_api_key"
                        value={String(settings.openai_api_key ?? "")}
                        onSave={updateSetting}
                        saving={saving}
                        saved={saved}
                        masked
                      />
                    </div>
                  )}

                  <Separator />

                  <div className="space-y-2">
                    <Label className="text-sm text-muted-foreground">润色阶段使用</Label>
                    <RadioGroup
                      value={settings.polish_provider ?? "local"}
                      onValueChange={(v) => updateSetting("polish_provider", v)}
                      className="flex flex-wrap gap-4"
                    >
                      <div className="flex items-center gap-2">
                        <RadioGroupItem value="" id="polish-global" />
                        <Label htmlFor="polish-global">跟随全局</Label>
                      </div>
                      <div className="flex items-center gap-2">
                        <RadioGroupItem value="local" id="polish-local" />
                        <Label htmlFor="polish-local">本地 HF</Label>
                      </div>
                      <div className="flex items-center gap-2">
                        <RadioGroupItem value="anthropic" id="polish-anthropic" />
                        <Label htmlFor="polish-anthropic">Anthropic</Label>
                      </div>
                      <div className="flex items-center gap-2">
                        <RadioGroupItem value="openai" id="polish-openai" />
                        <Label htmlFor="polish-openai">OpenAI</Label>
                      </div>
                      <div className="flex items-center gap-2">
                        <RadioGroupItem value="deepseek" id="polish-deepseek" />
                        <Label htmlFor="polish-deepseek">DeepSeek</Label>
                      </div>
                      <div className="flex items-center gap-2">
                        <RadioGroupItem value="custom" id="polish-custom" />
                        <Label htmlFor="polish-custom">Custom</Label>
                      </div>
                    </RadioGroup>
                  </div>
                </CardContent>
              </Card>

              {/* UVR */}
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="text-base">UVR 人声分离</CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                  <SettingRow
                    label="模型"
                    settingKey="uvr_model"
                    value={String(settings.uvr_model ?? "")}
                    onSave={updateSetting}
                    saving={saving}
                    saved={saved}
                    placeholder="模型名或绝对路径"
                  />
                  <SettingRow
                    label="设备"
                    settingKey="uvr_device"
                    value={String(settings.uvr_device ?? "cuda")}
                    onSave={updateSetting}
                    saving={saving}
                    saved={saved}
                  />
                </CardContent>
              </Card>

              {/* Voiceprint */}
              <VoiceprintCard settings={settings} updateSetting={updateSetting} />
            </>
          )}

          {/* ── 哔哩哔哩 ── */}
          {activeTab === "bilibili" && (
            <BilibiliCard onAuthChange={setBiliLoggedIn} />
          )}

          {/* ── YouTube ── */}
          {activeTab === "youtube" && (
            <YoutubeCard settings={settings} updateSetting={updateSetting} saving={saving} saved={saved} />
          )}

          {/* ── Coming soon platforms ── */}
          {activeTab === "xiaoyuzhou" && (
            <PlaceholderSection
              title="小宇宙"
              description="播客平台，支持 RSS 订阅下载"
            />
          )}
          {activeTab === "xiaohongshu" && (
            <PlaceholderSection
              title="小红书"
              description="短视频/笔记下载，需登录 Cookie"
            />
          )}
          {activeTab === "zhihu" && (
            <PlaceholderSection
              title="知乎"
              description="视频/专栏内容下载"
            />
          )}
        </div>
      </div>
    </div>
  )
}

interface YoutubeCardProps {
  settings: Settings
  updateSetting: (key: string, value: unknown) => Promise<void>
  saving: Record<string, boolean>
  saved: Record<string, boolean>
}

function YoutubeCard({ settings, updateSetting, saving, saved }: YoutubeCardProps) {
  const browser = String(settings.youtube_cookies_browser ?? "")
  const BROWSERS = ["", "chrome", "firefox", "edge", "brave", "opera", "vivaldi", "safari"]

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base">YouTube</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-xs text-muted-foreground">
          YouTube 频繁要求登录验证才能下载。任选一种方式提供 cookies：
          指定导出的 cookies.txt，或让 yt-dlp 直接读取浏览器 cookies（Chrome 运行时会锁数据库，建议用 Firefox/Edge 或关闭 Chrome）。
          两者都填则优先使用文件。
        </p>

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
            onChange={(e) => updateSetting("youtube_cookies_browser", e.target.value)}
            className="flex-1 h-8 text-sm rounded-md border border-input bg-background px-2"
          >
            {BROWSERS.map((b) => (
              <option key={b} value={b}>{b === "" ? "（不使用）" : b}</option>
            ))}
          </select>
        </div>
      </CardContent>
    </Card>
  )
}

interface VoiceprintCardProps {
  settings: Settings
  updateSetting: (key: string, value: unknown) => Promise<void>
}

function VoiceprintCard({ settings, updateSetting }: VoiceprintCardProps) {
  const enabled = Boolean(settings.enable_voiceprint ?? true)
  const serverMatch = Number(settings.voiceprint_match_threshold ?? 0.75)
  const serverSuggest = Number(settings.voiceprint_suggest_threshold ?? 0.60)

  const [match, setMatch] = useState(serverMatch)
  const [suggest, setSuggest] = useState(serverSuggest)

  // Sync with server changes
  useEffect(() => setMatch(serverMatch), [serverMatch])
  useEffect(() => setSuggest(serverSuggest), [serverSuggest])

  const MATCH_MIN = 0.50, MATCH_MAX = 0.90
  const SUGGEST_MIN = 0.40, SUGGEST_MAX = 0.80
  const GAP = 0.10

  const clampSuggest = (m: number, s: number) => {
    const ceiling = Math.min(SUGGEST_MAX, Math.round((m - GAP) * 100) / 100)
    return Math.max(SUGGEST_MIN, Math.min(ceiling, Math.round(s * 100) / 100))
  }

  const handleMatchChange = (v: number) => {
    const rounded = Math.round(v * 100) / 100
    setMatch(rounded)
    // Enforce suggest <= match - GAP
    const adjusted = clampSuggest(rounded, suggest)
    if (adjusted !== suggest) setSuggest(adjusted)
  }

  const handleSuggestChange = (v: number) => {
    const rounded = Math.round(v * 100) / 100
    const ceiling = Math.min(SUGGEST_MAX, Math.round((match - GAP) * 100) / 100)
    setSuggest(Math.min(rounded, ceiling))
  }

  const commitMatch = () => {
    if (match !== serverMatch) updateSetting("voiceprint_match_threshold", match)
    const adjusted = clampSuggest(match, suggest)
    if (adjusted !== serverSuggest) updateSetting("voiceprint_suggest_threshold", adjusted)
  }

  const commitSuggest = () => {
    if (suggest !== serverSuggest) updateSetting("voiceprint_suggest_threshold", suggest)
  }

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base">声纹识别</CardTitle>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="flex items-center justify-between">
          <Label>启用声纹识别</Label>
          <Switch
            checked={enabled}
            onCheckedChange={(v) => updateSetting("enable_voiceprint", Boolean(v))}
          />
        </div>

        {enabled && (
          <>
            <Separator />
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <Label className="text-sm text-muted-foreground">匹配阈值（自动合并）</Label>
                <span className="text-sm tabular-nums">{match.toFixed(2)}</span>
              </div>
              <Slider
                min={MATCH_MIN}
                max={MATCH_MAX}
                step={0.01}
                value={[match]}
                onValueChange={(v) => handleMatchChange(v[0])}
                onValueCommit={commitMatch}
              />
              <p className="text-xs text-muted-foreground">
                相似度 ≥ 此值时，说话人会被自动归入已存在的声纹。
              </p>
            </div>

            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <Label className="text-sm text-muted-foreground">待确认下限</Label>
                <span className="text-sm tabular-nums">{suggest.toFixed(2)}</span>
              </div>
              <Slider
                min={SUGGEST_MIN}
                max={Math.min(SUGGEST_MAX, Math.round((match - GAP) * 100) / 100)}
                step={0.01}
                value={[suggest]}
                onValueChange={(v) => handleSuggestChange(v[0])}
                onValueCommit={commitSuggest}
              />
              <p className="text-xs text-muted-foreground">
                必须 ≤ 匹配阈值 − 0.10。介于此值与匹配阈值之间会建立新身份但记录为可疑匹配。
              </p>
            </div>
          </>
        )}
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

function BilibiliCard({ onAuthChange }: BilibiliCardProps) {
  const [status, setStatus] = useState<{
    logged_in: boolean; uid?: string; expires?: string; days_left?: number; message?: string
  } | null>(null)

  const [platformConfig, setPlatformConfig] = useState<{
    preferred_quality: number
    prefer_subtitle: boolean
  } | null>(null)

  const [savedQuality, setSavedQuality] = useState(false)
  const [savedSubtitle, setSavedSubtitle] = useState(false)

  useEffect(() => {
    api.bilibili.status()
      .then((s) => {
        setStatus(s)
        onAuthChange?.(s.logged_in)
      })
      .catch(() => {
        setStatus({ logged_in: false, message: "无法连接后端" })
        onAuthChange?.(false)
      })
    api.platforms.list().then((res) => {
      const bili = res.platforms.find((p) => p.id === "bilibili")
      if (bili) {
        setPlatformConfig({ preferred_quality: Number(bili.preferred_quality ?? 64), prefer_subtitle: Boolean(bili.prefer_subtitle) })
      }
    }).catch(() => {})
  }, [])

  const handleQualityChange = async (value: number) => {
    if (!platformConfig) return
    setPlatformConfig((prev) => prev ? { ...prev, preferred_quality: value } : prev)
    try {
      await api.platforms.update("bilibili", { preferred_quality: value })
      setSavedQuality(true)
      setTimeout(() => setSavedQuality(false), 1500)
    } catch {}
  }

  const handleSubtitleChange = async (checked: boolean) => {
    if (!platformConfig) return
    setPlatformConfig((prev) => prev ? { ...prev, prefer_subtitle: checked } : prev)
    try {
      await api.platforms.update("bilibili", { prefer_subtitle: checked })
      setSavedSubtitle(true)
      setTimeout(() => setSavedSubtitle(false), 1500)
    } catch {}
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
                  onChange={(e) => handleQualityChange(Number(e.target.value))}
                  className="flex-1 h-8 rounded-md border border-input bg-background px-2 text-sm disabled:opacity-50"
                >
                  {BILIBILI_QUALITY_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>{opt.label}</option>
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
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}

// --- DeepSeek per-stage config ---

interface DeepSeekConfigProps {
  settings: Settings
  updateSetting: (key: string, value: unknown) => Promise<void>
  saving: Record<string, boolean>
  saved: Record<string, boolean>
}

const DS_STAGES: Array<{ key: "analyze" | "polish" | "summary" | "mindmap"; label: string; hint: string }> = [
  { key: "analyze", label: "Analyze", hint: "第一阶段：从字幕抽取上下文（语言、主题、专名等）" },
  { key: "polish", label: "Polish", hint: "字幕润色（量大，推荐 flash + no-think）" },
  { key: "summary", label: "Summary", hint: "总结/README（质量优先，推荐 pro + thinking + max）" },
  { key: "mindmap", label: "Mindmap", hint: "思维导图 map+reduce（推荐 flash + no-think）" },
]

function DeepSeekConfig({ settings, updateSetting, saving, saved }: DeepSeekConfigProps) {
  return (
    <div className="space-y-4">
      <p className="text-xs text-muted-foreground">
        DeepSeek v4 原生 API，按阶段独立配置 model / thinking / reasoning_effort。
        deepseek-chat 与 deepseek-reasoner 已于 2026/07/24 弃用，请使用 deepseek-v4-flash 或 deepseek-v4-pro。
      </p>
      <SettingRow
        label="API Base"
        settingKey="deepseek_api_base"
        value={String(settings.deepseek_api_base ?? "https://api.deepseek.com")}
        onSave={updateSetting}
        saving={saving}
        saved={saved}
      />
      <SettingRow
        label="API Key"
        settingKey="deepseek_api_key"
        value={String(settings.deepseek_api_key ?? "")}
        onSave={updateSetting}
        saving={saving}
        saved={saved}
        masked
      />
      <Separator />
      {DS_STAGES.map((stage) => (
        <DeepSeekStageBlock
          key={stage.key}
          stage={stage.key}
          label={stage.label}
          hint={stage.hint}
          settings={settings}
          updateSetting={updateSetting}
          saving={saving}
          saved={saved}
        />
      ))}
    </div>
  )
}

interface DeepSeekStageBlockProps extends DeepSeekConfigProps {
  stage: "analyze" | "polish" | "summary" | "mindmap"
  label: string
  hint: string
}

function DeepSeekStageBlock({
  stage, label, hint, settings, updateSetting, saving, saved,
}: DeepSeekStageBlockProps) {
  const modelKey = `deepseek_${stage}_model`
  const thinkingKey = `deepseek_${stage}_thinking`
  const effortKey = `deepseek_${stage}_effort`

  const thinking = String(settings[thinkingKey] ?? "disabled")
  const effort = String(settings[effortKey] ?? "")

  return (
    <div className="space-y-2 rounded-md border border-border p-3">
      <div className="flex items-baseline justify-between">
        <Label className="text-sm font-medium">{label}</Label>
        <span className="text-xs text-muted-foreground">{hint}</span>
      </div>
      <SettingRow
        label={<span className="flex items-center gap-1.5">模型{isNewModel(String(settings[modelKey] ?? "")) && <NewBadge />}</span>}
        settingKey={modelKey}
        value={String(settings[modelKey] ?? "")}
        onSave={updateSetting}
        saving={saving}
        saved={saved}
        placeholder="deepseek-v4-flash / deepseek-v4-pro"
      />
      <div className="flex items-center gap-3">
        <Label className="w-24 shrink-0 text-sm text-muted-foreground">Thinking</Label>
        <select
          value={thinking}
          onChange={(e) => updateSetting(thinkingKey, e.target.value)}
          className="h-8 rounded-md border border-input bg-background px-2 text-sm"
        >
          <option value="disabled">disabled</option>
          <option value="enabled">enabled</option>
        </select>
      </div>
      <div className="flex items-center gap-3">
        <Label className="w-24 shrink-0 text-sm text-muted-foreground">Effort</Label>
        <select
          value={effort}
          onChange={(e) => updateSetting(effortKey, e.target.value)}
          disabled={thinking !== "enabled"}
          className="h-8 rounded-md border border-input bg-background px-2 text-sm disabled:opacity-50"
        >
          <option value="">（默认 high）</option>
          <option value="high">high</option>
          <option value="max">max</option>
        </select>
      </div>
    </div>
  )
}

// --- Reusable setting row ---

interface SettingRowProps {
  label: React.ReactNode
  settingKey: string
  value: string
  onSave: (key: string, value: unknown) => Promise<void>
  saving: Record<string, boolean>
  saved: Record<string, boolean>
  masked?: boolean
  placeholder?: string
}

function SettingRow({ label, settingKey, value, onSave, saving, saved, masked, placeholder }: SettingRowProps) {
  const [editValue, setEditValue] = useState(value)

  // Sync with external value
  useEffect(() => {
    setEditValue(value)
  }, [value])

  const isDirty = editValue !== value
  const isSaving = saving[settingKey]
  const isSaved = saved[settingKey]

  const handleSave = () => {
    if (!isDirty) return
    onSave(settingKey, editValue)
  }

  return (
    <div className="flex items-center gap-3">
      <Label className="w-24 shrink-0 text-sm text-muted-foreground">{label}</Label>
      <Input
        type={masked ? "password" : "text"}
        value={editValue}
        onChange={(e) => setEditValue(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && handleSave()}
        className="flex-1 h-8 text-sm"
        autoComplete="off"
        placeholder={placeholder}
      />
      {isDirty && (
        <Button
          size="sm"
          variant="ghost"
          onClick={handleSave}
          disabled={isSaving}
          className="h-8 px-2"
        >
          {isSaved ? <HugeiconsIcon icon={Tick02Icon} className="h-3.5 w-3.5" /> : <HugeiconsIcon icon={FloppyDiskIcon} className="h-3.5 w-3.5" />}
        </Button>
      )}
      {!isDirty && isSaved && (
        <HugeiconsIcon icon={Tick02Icon} className="h-3.5 w-3.5 text-emerald-500" />
      )}
    </div>
  )
}
