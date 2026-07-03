import { useEffect, useState, useCallback } from "react"
import React from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group"
import { Label } from "@/components/ui/label"
import { Separator } from "@/components/ui/separator"
import { Switch } from "@/components/ui/switch"
import { api, type Settings } from "@/lib/api"
import { usePreferences } from "@/hooks/use-preferences"
import { SettingRow } from "@/components/settings/setting-controls"
import { LocalModelSettings, PurposeModelBindings, RegistrySettings } from "@/components/settings/model-sections"
import { BilibiliCard, PlaceholderSection, YoutubeCard, ZhihuCard } from "@/components/settings/source-cards"
import { HugeiconsIcon } from "@hugeicons/react"
import { Tick02Icon, Moon02Icon, Sun01Icon } from "@hugeicons/core-free-icons"

// --- Tab definitions ---

type TabId = "overall" | "knowledge" | "registry" | "services" | "local" | "pipelines"

interface TabDef {
  id: TabId
  label: string
}

const TABS: TabDef[] = [
  { id: "overall", label: "Overall" },
  { id: "knowledge", label: "Knowledge Base" },
  { id: "registry", label: "Providers" },
  { id: "services", label: "Services" },
  { id: "local", label: "Local Models" },
  { id: "pipelines", label: "Pipelines/Sources" },
]

export function SettingsPanel() {
  const [settings, setSettings] = useState<Settings | null>(null)
  const [saving, setSaving] = useState<Record<string, boolean>>({})
  const [saved, setSaved] = useState<Record<string, boolean>>({})
  const [saveError, setSaveError] = useState<string | null>(null)
  const [darkMode, setDarkMode] = useState(
    () => document.documentElement.classList.contains("dark"),
  )
  const { prefs, update: updatePrefs } = usePreferences()
  const [activeTab, setActiveTab] = useState<TabId>("overall")
  const [uvrDetecting, setUvrDetecting] = useState(false)
  const [uvrDetection, setUvrDetection] = useState<string | null>(null)

  // Bilibili auth status (needed for sidebar dot indicator)
  const [biliLoggedIn, setBiliLoggedIn] = useState<boolean | null>(null)

  useEffect(() => {
    api.settings.get().then(setSettings).catch(() => {})
    api.bilibili.status()
      .then((s) => setBiliLoggedIn(s.logged_in))
      .catch(() => setBiliLoggedIn(false))
  }, [])

  const updateSettings = useCallback(
    async (updates: Record<string, unknown>) => {
      const keys = Object.keys(updates)
      setSaving((s) => keys.reduce((acc, key) => ({ ...acc, [key]: true }), s))
      try {
        const updated = await api.settings.patch(updates)
        setSettings(updated)
        setSaveError(null)
        setSaved((s) => keys.reduce((acc, key) => ({ ...acc, [key]: true }), s))
        setTimeout(() => {
          setSaved((s) => keys.reduce((acc, key) => ({ ...acc, [key]: false }), s))
        }, 1500)
      } catch (e) {
        setSaveError(e instanceof Error ? e.message : String(e))
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

  const toggleDark = () => {
    const next = !darkMode
    setDarkMode(next)
    document.documentElement.classList.toggle("dark", next)
    localStorage.setItem("theme", next ? "dark" : "light")
  }

  if (!settings) {
    return <p className="text-sm text-muted-foreground">加载中...</p>
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

  const visibleLlmProvider = ["local", "deepseek", "custom", "anthropic", "openai"].includes(settings.llm_provider)
    ? settings.llm_provider
    : "deepseek"

  return (
    <div className="h-full min-h-0 w-full">
      {saveError && (
        <div className="mb-4 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {saveError}
        </div>
      )}
      <div className="flex h-full min-h-0 gap-5">
        {/* Left sidebar */}
        <nav className="sticky top-5 h-fit w-[220px] shrink-0 space-y-1 rounded-lg border bg-card p-2">
          {TABS.map((tab) => {
            const isActive = activeTab === tab.id

            return (
              <React.Fragment key={tab.id}>
                <button
                  onClick={() => setActiveTab(tab.id)}
                  className={[
                    "w-full text-left flex items-center gap-2 px-3 py-2 rounded-md text-sm transition-colors",
                    isActive
                      ? "bg-primary/10 text-primary font-medium"
                      : "text-muted-foreground hover:text-foreground hover:bg-accent/50",
                  ].join(" ")}
                >
                  <span className="truncate flex-1">{tab.label}</span>
                  {tab.id === "pipelines" && biliLoggedIn !== null && (
                    <span
                      className={[
                        "h-1.5 w-1.5 rounded-full shrink-0",
                        biliLoggedIn ? "bg-emerald-500" : "bg-red-500",
                      ].join(" ")}
                    />
                  )}
                </button>
              </React.Fragment>
            )
          })}
        </nav>

        {/* Right content */}
        <div className="min-w-0 flex-1 overflow-hidden [&_[data-slot=card]]:rounded-none [&_[data-slot=card]]:bg-transparent [&_[data-slot=card]]:py-0 [&_[data-slot=card]]:ring-0 [&_[data-slot=card]]:border-b [&_[data-slot=card]]:border-border/70 [&_[data-slot=card]]:pb-4 [&_[data-slot=card-header]]:px-0 [&_[data-slot=card-header]]:pb-1.5 [&_[data-slot=card-content]]:px-0">
          {/* ── Overall ── */}
          {activeTab === "overall" && (
            <div className="h-full min-h-0 space-y-4 overflow-y-auto pr-1">
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

              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="text-base">网络</CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                  <SettingRow
                    label="代理"
                    settingKey="network_proxy"
                    value={String(settings.network_proxy ?? "")}
                    onSave={updateSetting}
                    saving={saving}
                    saved={saved}
                    placeholder="http://127.0.0.1:7897"
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

              <PurposeModelBindings settings={settings} updateSetting={updateSetting} />

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
            </div>
          )}

          {/* ── LLM/API Registry ── */}
          {activeTab === "registry" && (
            <RegistrySettings
              settings={settings}
              visibleLlmProvider={visibleLlmProvider}
              updateSetting={updateSetting}
              updateSettings={updateSettings}
              saving={saving}
              saved={saved}
            />
          )}

          {activeTab === "local" && (
            <LocalModelSettings
              settings={settings}
              updateSetting={updateSetting}
              saving={saving}
              saved={saved}
              detectLocalUvr={detectLocalUvr}
              uvrDetecting={uvrDetecting}
              uvrDetection={uvrDetection}
            />
          )}

          {/* ── Services ── */}
          {activeTab === "services" && (
            <div className="h-full min-h-0 space-y-4 overflow-y-auto pr-1">
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="text-base">Jina</CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                  <p className="text-xs leading-5 text-muted-foreground">
                    网页 Scrape fallback 服务。通用网页先使用本地 Defuddle，失败后调用 Jina Reader 返回 Markdown。
                  </p>
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
                    label="API Base"
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
            </div>
          )}

          {/* ── Knowledge Base ── */}
          {activeTab === "knowledge" && (
            <div className="h-full min-h-0 space-y-4 overflow-y-auto pr-1">
              {/* Knowledge base */}
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="text-base">知识库 Embedding</CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                  <p className="text-xs text-muted-foreground">OpenAI-Compatible 嵌入 API，用于任务完成后自动索引字幕+摘要。</p>
                  <div className="flex items-center gap-3">
                    <Label className="text-sm min-w-max">自动索引</Label>
                    <input
                      type="checkbox"
                      checked={Boolean(settings.kb_enabled ?? true)}
                      onChange={(e) => updateSetting("kb_enabled", e.target.checked)}
                      className="h-4 w-4 accent-primary cursor-pointer"
                    />
                  </div>
                  <SettingRow
                    label="API Base"
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
                  />
                </CardContent>
              </Card>
            </div>
          )}

          {/* ── Pipelines/Sources ── */}
          {activeTab === "pipelines" && (
            <div className="h-full min-h-0 space-y-4 overflow-y-auto pr-1">
              <BilibiliCard onAuthChange={setBiliLoggedIn} />
              <YoutubeCard settings={settings} updateSetting={updateSetting} saving={saving} saved={saved} />
              <PlaceholderSection
                title="小宇宙"
                description="已支持公开单集页面：提取页面元数据、下载 m4a，并转为本地 ASR 使用的 wav。"
                comingSoon={false}
              />
              <PlaceholderSection
                title="小红书"
                description="已支持公开视频笔记：解析分享链接/短链、下载 mp4，并提取音频进入本地 ASR。私密或风控笔记可配置 Cookie 后重试。"
                comingSoon={false}
              />
              <ZhihuCard settings={settings} updateSetting={updateSetting} saving={saving} saved={saved} />
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
