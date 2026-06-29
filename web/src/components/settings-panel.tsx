import { useEffect, useState, useCallback } from "react"
import React from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group"
import { Label } from "@/components/ui/label"
import { Separator } from "@/components/ui/separator"
import { Switch } from "@/components/ui/switch"
import { api, type Settings } from "@/lib/api"
import { usePreferences } from "@/hooks/use-preferences"
import { DeviceChoice, PathPickerRow, SettingRow } from "@/components/settings/setting-controls"
import { LocalModelSettings, RegistrySettings } from "@/components/settings/model-sections"
import { BilibiliCard, PlaceholderSection, YoutubeCard, ZhihuCard } from "@/components/settings/source-cards"
import { HugeiconsIcon } from "@hugeicons/react"
import { Tick02Icon, Moon02Icon, Sun01Icon } from "@hugeicons/core-free-icons"

// --- Tab definitions ---

type TabId = "overall" | "knowledge" | "registry" | "local" | "pipelines"

interface TabDef {
  id: TabId
  label: string
}

const TABS: TabDef[] = [
  { id: "overall", label: "Overall" },
  { id: "knowledge", label: "Knowledge Base" },
  { id: "registry", label: "LLM/API Registry" },
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
    <div className="w-full">
      {saveError && (
        <div className="mb-4 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {saveError}
        </div>
      )}
      <div className="flex min-h-[calc(100vh-170px)] gap-5">
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
        <div className="min-w-0 flex-1 space-y-4 [&_[data-slot=card]]:rounded-none [&_[data-slot=card]]:bg-transparent [&_[data-slot=card]]:py-0 [&_[data-slot=card]]:ring-0 [&_[data-slot=card]]:border-b [&_[data-slot=card]]:border-border/70 [&_[data-slot=card]]:pb-4 [&_[data-slot=card-header]]:px-0 [&_[data-slot=card-header]]:pb-1.5 [&_[data-slot=card-content]]:px-0">
          {/* ── Overall ── */}
          {activeTab === "overall" && (
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
            </>
          )}

          {/* ── Local Models ── */}
          {activeTab === "local" && (
            <>
              {/* ASR */}
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="text-base flex items-center gap-2">
                    ASR
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  <RadioGroup
                    value={String(settings.asr_provider ?? "qwen3")}
                    onValueChange={(v) => updateSetting("asr_provider", v)}
                    className="flex flex-wrap gap-4"
                  >
                    <div className="flex items-center gap-2">
                      <RadioGroupItem value="qwen3" id="asr-qwen3" />
                      <Label htmlFor="asr-qwen3">Qwen3-ASR（本地）</Label>
                    </div>
                    <div className="flex items-center gap-2">
                      <RadioGroupItem value="siliconflow" id="asr-siliconflow" />
                      <Label htmlFor="asr-siliconflow">SiliconFlow API</Label>
                    </div>
                  </RadioGroup>
                  <Separator />
                  {String(settings.asr_provider ?? "qwen3") === "siliconflow" ? (
                    <div className="space-y-3">
                      <p className="text-xs text-muted-foreground">
                        通过 OpenAI 兼容的 /audio/transcriptions 接口调用。本地 Silero VAD 切片后串行上传，时间戳由 VAD 边界给出。
                      </p>
                      <SettingRow
                        label="API Base"
                        settingKey="siliconflow_api_base"
                        value={String(settings.siliconflow_api_base ?? "https://api.siliconflow.cn/v1")}
                        onSave={updateSetting}
                        saving={saving}
                        saved={saved}
                        placeholder="https://api.siliconflow.cn/v1"
                      />
                      <SettingRow
                        label="API Key"
                        settingKey="siliconflow_api_key"
                        value={String(settings.siliconflow_api_key ?? "")}
                        onSave={updateSetting}
                        saving={saving}
                        saved={saved}
                        masked
                      />
                      <SettingRow
                        label="模型"
                        settingKey="siliconflow_asr_model"
                        value={String(settings.siliconflow_asr_model ?? "FunAudioLLM/SenseVoiceSmall")}
                        onSave={updateSetting}
                        saving={saving}
                        saved={saved}
                        placeholder="FunAudioLLM/SenseVoiceSmall"
                      />
                      <SettingRow
                        label="语言（空=自动）"
                        settingKey="siliconflow_asr_language"
                        value={String(settings.siliconflow_asr_language ?? "")}
                        onSave={updateSetting}
                        saving={saving}
                        saved={saved}
                        placeholder="zh / en / 留空自动"
                      />
                      <SettingRow
                        label="切片上限（秒）"
                        settingKey="siliconflow_asr_max_chunk_sec"
                        value={String(settings.siliconflow_asr_max_chunk_sec ?? 30)}
                        onSave={(key, val) => updateSetting(key, Number(val))}
                        saving={saving}
                        saved={saved}
                      />
                      <SettingRow
                        label="单段超时（秒）"
                        settingKey="siliconflow_asr_timeout_sec"
                        value={String(settings.siliconflow_asr_timeout_sec ?? 120)}
                        onSave={(key, val) => updateSetting(key, Number(val))}
                        saving={saving}
                        saved={saved}
                      />
                    </div>
                  ) : (
                    <div className="space-y-3">
                      <PathPickerRow
                        label="模型路径"
                        settingKey="qwen3_asr_model_path"
                        value={String(settings.qwen3_asr_model_path ?? "")}
                        onSave={updateSetting}
                        saving={saving}
                        saved={saved}
                        placeholder="留空使用 HuggingFace，或选择本地模型目录"
                        title="选择 Qwen3-ASR 模型目录"
                      />
                      <DeviceChoice
                        value={String(settings.qwen3_device ?? "cuda")}
                        onChange={(value) => updateSetting("qwen3_device", value)}
                      />
                      <PathPickerRow
                        label="对齐模型"
                        settingKey="qwen3_aligner_model_path"
                        value={String(settings.qwen3_aligner_model_path ?? "")}
                        onSave={updateSetting}
                        saving={saving}
                        saved={saved}
                        placeholder="可选：Qwen3-ForcedAligner 本地目录"
                        title="选择 Qwen3 ForcedAligner 模型目录"
                      />
                      <Separator />
                      <div className="flex items-center justify-between">
                        <div>
                          <Label>说话人分离</Label>
                          <p className="text-xs text-muted-foreground mt-0.5">
                            使用 pyannote 给字幕段落标注 SPEAKER_XX，并为声纹识别提供切片。
                          </p>
                        </div>
                        <Switch
                          checked={Boolean(settings.enable_diarization ?? true)}
                          onCheckedChange={(v) => updateSetting("enable_diarization", Boolean(v))}
                        />
                      </div>
                      {Boolean(settings.enable_diarization ?? true) && (
                        <div className="space-y-3">
                          <PathPickerRow
                            label="Diarization"
                            settingKey="pyannote_model_path"
                            value={String(settings.pyannote_model_path ?? "")}
                            onSave={updateSetting}
                            saving={saving}
                            saved={saved}
                            placeholder="pyannote-speaker-diarization-3.1 本地目录"
                            title="选择 pyannote diarization 模型目录"
                          />
                          <PathPickerRow
                            label="Segmentation"
                            settingKey="pyannote_segmentation_path"
                            value={String(settings.pyannote_segmentation_path ?? "")}
                            onSave={updateSetting}
                            saving={saving}
                            saved={saved}
                            placeholder="pyannote-segmentation-3.0 本地目录"
                            title="选择 pyannote segmentation 模型目录"
                          />
                          <PathPickerRow
                            label="Embedding"
                            settingKey="pyannote_embedding_path"
                            value={String(settings.pyannote_embedding_path ?? "")}
                            onSave={updateSetting}
                            saving={saving}
                            saved={saved}
                            placeholder="pyannote_wespeaker-voxceleb-resnet34-LM 本地目录"
                            title="选择 pyannote embedding 模型目录"
                          />
                          <SettingRow
                            label="HF Proxy"
                            settingKey="hf_proxy"
                            value={String(settings.hf_proxy ?? "")}
                            onSave={updateSetting}
                            saving={saving}
                            saved={saved}
                            masked
                            placeholder="留空自动读取系统代理，direct 禁用"
                          />
                          <SettingRow
                            label="HF Token"
                            settingKey="hf_token"
                            value={String(settings.hf_token ?? "")}
                            onSave={updateSetting}
                            saving={saving}
                            saved={saved}
                            masked
                          />
                          <SettingRow
                            label="分离批量"
                            settingKey="diarization_batch_size"
                            value={String(settings.diarization_batch_size ?? 16)}
                            onSave={(key, val) => updateSetting(key, Number(val))}
                            saving={saving}
                            saved={saved}
                          />
                        </div>
                      )}
                    </div>
                  )}
                </CardContent>
              </Card>
            </>
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

          {/* ── Knowledge Base ── */}
          {activeTab === "knowledge" && (
            <>
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
            </>
          )}

          {/* ── Pipelines/Sources ── */}
          {activeTab === "pipelines" && (
            <>
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
            </>
          )}
        </div>
      </div>
    </div>
  )
}
