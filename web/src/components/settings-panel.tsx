import { useEffect, useState, useCallback } from "react"
import React from "react"
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
import { getDialogBridge } from "@/lib/electron"
import { usePreferences } from "@/hooks/use-preferences"
import { HugeiconsIcon } from "@hugeicons/react"
import { FloppyDiskIcon, Tick02Icon, Moon02Icon, Sun01Icon, FolderOpenIcon, PlusSignIcon, Delete01Icon } from "@hugeicons/core-free-icons"

function ComingSoonBadge() {
  return (
    <Badge variant="outline" className="text-[10px] px-1.5 py-0">即将</Badge>
  )
}

function ModelLabel({ name }: { name: string }) {
  return (
    <span className="flex items-center gap-1.5">
      {name}
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
  { id: "xiaoyuzhou", label: "小宇宙" },
  { id: "xiaohongshu", label: "小红书" },
  { id: "zhihu", label: "知乎" },
]

// --- Placeholder section for coming-soon platforms ---

interface PlaceholderSectionProps {
  title: string
  description: string
  comingSoon?: boolean
}

function PlaceholderSection({ title, description, comingSoon = true }: PlaceholderSectionProps) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base flex items-center gap-2">
          {title}
          {comingSoon ? <ComingSoonBadge /> : <Badge variant="outline" className="text-[10px] px-1.5 py-0">已支持</Badge>}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-sm text-muted-foreground">{description}</p>
      </CardContent>
    </Card>
  )
}

type DeviceValue = "cuda" | "cpu"

function DeviceChoice({
  value,
  onChange,
  labels = { cuda: "CUDA", cpu: "内存" },
}: {
  value: string
  onChange: (value: DeviceValue) => void
  labels?: Record<DeviceValue, string>
}) {
  const current: DeviceValue = value === "cpu" ? "cpu" : "cuda"
  return (
    <div className="flex items-center gap-3">
      <Label className="w-24 shrink-0 text-sm text-muted-foreground">设备</Label>
      <div className="flex items-center gap-1">
        {(["cuda", "cpu"] as const).map((device) => (
          <button
            key={device}
            type="button"
            onClick={() => onChange(device)}
            className={[
              "h-8 px-3 text-sm transition-colors",
              current === device
                ? "text-primary font-medium border-b-2 border-primary"
                : "text-muted-foreground hover:text-foreground",
            ].join(" ")}
          >
            {labels[device]}
          </button>
        ))}
      </div>
    </div>
  )
}

function PathPickerRow({
  label,
  settingKey,
  value,
  onSave,
  saving,
  saved,
  placeholder,
  title,
}: SettingRowProps & { title?: string }) {
  const [editValue, setEditValue] = useState(value)

  useEffect(() => setEditValue(value), [value])

  const pickDirectory = async () => {
    const selected = await getDialogBridge()?.selectDirectory({
      title: title ?? "选择模型文件夹",
      defaultPath: editValue || undefined,
    })
    if (selected) {
      setEditValue(selected)
      await onSave(settingKey, selected)
      return
    }
    if (!getDialogBridge()) {
      const manual = window.prompt("输入文件夹路径", editValue)
      if (manual !== null) {
        setEditValue(manual)
        await onSave(settingKey, manual)
      }
    }
  }

  const isDirty = editValue !== value
  const isSaving = saving[settingKey]
  const isSaved = saved[settingKey]

  return (
    <div className="flex items-center gap-3">
      <Label className="w-24 shrink-0 text-sm text-muted-foreground">{label}</Label>
      <Input
        value={editValue}
        onChange={(e) => setEditValue(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && onSave(settingKey, editValue)}
        className="h-8 flex-1 text-sm"
        autoComplete="off"
        placeholder={placeholder}
      />
      <Button size="sm" variant="ghost" onClick={pickDirectory} className="h-8 gap-1.5 px-2">
        <HugeiconsIcon icon={FolderOpenIcon} className="h-3.5 w-3.5" />
        选择
      </Button>
      {isDirty && (
        <Button size="sm" variant="ghost" onClick={() => onSave(settingKey, editValue)} disabled={isSaving} className="h-8 px-2">
          {isSaved ? <HugeiconsIcon icon={Tick02Icon} className="h-3.5 w-3.5" /> : <HugeiconsIcon icon={FloppyDiskIcon} className="h-3.5 w-3.5" />}
        </Button>
      )}
      {!isDirty && isSaved && (
        <HugeiconsIcon icon={Tick02Icon} className="h-3.5 w-3.5 text-emerald-500" />
      )}
    </div>
  )
}

interface CustomProfile {
  id: string
  name: string
  api_base: string
  model: string
  api_key: string
}

function getCustomProfiles(settings: Settings): CustomProfile[] {
  const raw = settings.custom_llm_profiles
  const profiles = Array.isArray(raw) ? raw : []
  if (profiles.length > 0) {
    return profiles.map((item, index) => {
      const data = item as Record<string, unknown>
      return {
        id: String(data.id ?? `custom-${index}`),
        name: String(data.name ?? data.custom_name ?? `Custom ${index + 1}`),
        api_base: String(data.api_base ?? data.custom_api_base ?? ""),
        model: String(data.model ?? data.custom_model ?? ""),
        api_key: String(data.api_key ?? data.custom_api_key ?? ""),
      }
    })
  }
  return [{
    id: "default",
    name: String(settings.custom_name ?? "Custom"),
    api_base: String(settings.custom_api_base ?? ""),
    model: String(settings.custom_model ?? ""),
    api_key: String(settings.custom_api_key ?? ""),
  }]
}

function CustomProfilesEditor({
  settings,
  updateSetting,
}: {
  settings: Settings
  updateSetting: (key: string, value: unknown) => Promise<void>
}) {
  const profiles = getCustomProfiles(settings)
  const activeId = String(settings.custom_active_profile_id ?? profiles[0]?.id ?? "default")
  const activeProfile = profiles.find((profile) => profile.id === activeId) ?? profiles[0]

  const saveProfiles = async (next: CustomProfile[], nextActive = activeId) => {
    const active = next.find((profile) => profile.id === nextActive) ?? next[0]
    await updateSetting("custom_llm_profiles", next)
    await updateSetting("custom_active_profile_id", active.id)
    await updateSetting("custom_name", active.name)
    await updateSetting("custom_api_base", active.api_base)
    await updateSetting("custom_model", active.model)
    await updateSetting("custom_api_key", active.api_key)
  }

  const updateProfile = (field: keyof CustomProfile, value: string) => {
    const next = profiles.map((profile) =>
      profile.id === activeProfile.id ? { ...profile, [field]: value } : profile,
    )
    void saveProfiles(next, activeProfile.id)
  }

  const addProfile = () => {
    const nextProfile: CustomProfile = {
      id: `custom-${Date.now()}`,
      name: `Custom ${profiles.length + 1}`,
      api_base: "",
      model: "",
      api_key: "",
    }
    void saveProfiles([...profiles, nextProfile], nextProfile.id)
  }

  const removeProfile = () => {
    if (profiles.length <= 1) return
    const next = profiles.filter((profile) => profile.id !== activeProfile.id)
    void saveProfiles(next, next[0].id)
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3">
        <Label className="w-24 shrink-0 text-sm text-muted-foreground">配置</Label>
        <select
          value={activeProfile.id}
          onChange={(e) => void saveProfiles(profiles, e.target.value)}
          className="h-8 min-w-52 rounded-md border border-input bg-background px-3 text-sm"
        >
          {profiles.map((profile) => (
            <option key={profile.id} value={profile.id}>{profile.name || profile.id}</option>
          ))}
        </select>
        <Button size="sm" variant="ghost" onClick={addProfile} className="h-8 gap-1.5 px-2">
          <HugeiconsIcon icon={PlusSignIcon} className="h-3.5 w-3.5" />
          新增
        </Button>
        <Button size="sm" variant="ghost" onClick={removeProfile} disabled={profiles.length <= 1} className="h-8 gap-1.5 px-2 text-destructive hover:text-destructive">
          <HugeiconsIcon icon={Delete01Icon} className="h-3.5 w-3.5" />
          删除
        </Button>
      </div>
      <div className="grid gap-3 lg:grid-cols-2">
        <SettingRow label="名称" settingKey="custom_profile_name" value={activeProfile.name} onSave={async (_key, value) => updateProfile("name", String(value))} saving={{}} saved={{}} />
        <SettingRow label="模型" settingKey="custom_profile_model" value={activeProfile.model} onSave={async (_key, value) => updateProfile("model", String(value))} saving={{}} saved={{}} />
        <SettingRow label="API Base" settingKey="custom_profile_base" value={activeProfile.api_base} onSave={async (_key, value) => updateProfile("api_base", String(value))} saving={{}} saved={{}} />
        <SettingRow label="API Key" settingKey="custom_profile_key" value={activeProfile.api_key} onSave={async (_key, value) => updateProfile("api_key", String(value))} saving={{}} saved={{}} masked />
      </div>
    </div>
  )
}

export function SettingsPanel() {
  const [settings, setSettings] = useState<Settings | null>(null)
  const [saving, setSaving] = useState<Record<string, boolean>>({})
  const [saved, setSaved] = useState<Record<string, boolean>>({})
  const [saveError, setSaveError] = useState<string | null>(null)
  const [darkMode, setDarkMode] = useState(
    () => document.documentElement.classList.contains("dark"),
  )
  const { prefs, update: updatePrefs } = usePreferences()
  const [activeTab, setActiveTab] = useState<TabId>("general")
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

  const updateSetting = useCallback(
    async (key: string, value: unknown) => {
      setSaving((s) => ({ ...s, [key]: true }))
      try {
        const updated = await api.settings.patch({ [key]: value })
        setSettings(updated)
        setSaveError(null)
        setSaved((s) => ({ ...s, [key]: true }))
        setTimeout(() => setSaved((s) => ({ ...s, [key]: false })), 1500)
      } catch (e) {
        setSaveError(e instanceof Error ? e.message : String(e))
      }
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

  const visibleLlmProvider = ["local", "deepseek", "custom"].includes(settings.llm_provider)
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
                    "w-full text-left flex items-center gap-2 px-3 py-2 rounded-md text-sm transition-colors",
                    isActive
                      ? "bg-primary/10 text-primary font-medium"
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

        {/* Right content */}
        <div className="min-w-0 flex-1 space-y-4 [&_[data-slot=card]]:rounded-none [&_[data-slot=card]]:bg-transparent [&_[data-slot=card]]:py-0 [&_[data-slot=card]]:ring-0 [&_[data-slot=card]]:border-b [&_[data-slot=card]]:border-border/70 [&_[data-slot=card]]:pb-4 [&_[data-slot=card-header]]:px-0 [&_[data-slot=card-header]]:pb-1.5 [&_[data-slot=card-content]]:px-0">
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

          {/* ── AI 模型 ── */}
          {activeTab === "ai" && (
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
                    </div>
                  )}
                </CardContent>
              </Card>

              {/* LLM Provider */}
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="text-base">LLM 提供商</CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  <RadioGroup
                    value={visibleLlmProvider}
                    onValueChange={(v) => updateSetting("llm_provider", v)}
                    className="flex flex-wrap gap-4"
                  >
                    <div className="flex items-center gap-2">
                      <RadioGroupItem value="deepseek" id="llm-deepseek" />
                      <Label htmlFor="llm-deepseek">DeepSeek</Label>
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

                  {visibleLlmProvider === "local" ? (
                    <div className="space-y-3">
                      <PathPickerRow
                        label="模型目录"
                        settingKey="local_llm_model_path"
                        value={String(settings.local_llm_model_path ?? "")}
                        onSave={updateSetting}
                        saving={saving}
                        saved={saved}
                        placeholder="包含 config.json 和 *.safetensors 的目录"
                        title="选择本地 LLM 模型目录"
                      />
                      <DeviceChoice
                        value={String(settings.local_llm_device ?? "cuda")}
                        onChange={(value) => updateSetting("local_llm_device", value)}
                      />
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
                  ) : visibleLlmProvider === "custom" ? (
                    <CustomProfilesEditor settings={settings} updateSetting={updateSetting} />
                  ) : visibleLlmProvider === "deepseek" ? (
                    <DeepSeekConfig
                      settings={settings}
                      updateSetting={updateSetting}
                      saving={saving}
                      saved={saved}
                    />
                  ) : settings.llm_provider === "anthropic" ? (
                    <div className="space-y-3">
                      <SettingRow
                        label="模型"
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
                      value={["", "local", "deepseek", "custom"].includes(String(settings.polish_provider ?? "local")) ? String(settings.polish_provider ?? "local") : ""}
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
                  <div className="flex flex-wrap items-center gap-3">
                    <Button size="sm" variant="outline" onClick={detectLocalUvr} disabled={uvrDetecting} className="h-8">
                      {uvrDetecting ? "检查中..." : "检查本机 UVR"}
                    </Button>
                    {uvrDetection && <span className="text-xs text-muted-foreground">{uvrDetection}</span>}
                  </div>
                  <PathPickerRow
                    label="模型目录"
                    settingKey="uvr_model_dir"
                    value={String(settings.uvr_model_dir ?? "")}
                    onSave={updateSetting}
                    saving={saving}
                    saved={saved}
                    placeholder="选择本机 UVR models 目录；留空则自动扫描/下载"
                    title="选择 UVR 模型目录"
                  />
                  <div className="flex items-center gap-3">
                    <Label className="w-24 shrink-0 text-sm text-muted-foreground">模型</Label>
                    <select
                      value={String(settings.uvr_model ?? "UVR-MDX-NET-Inst_HQ_3")}
                      onChange={(e) => updateSetting("uvr_model", e.target.value)}
                      className="h-8 min-w-64 rounded-md border border-input bg-background px-3 text-sm"
                    >
                      {["UVR-MDX-NET-Inst_HQ_3", "1_HP-UVR", "UVR-DeNoise-Lite", "Kim_Vocal_2", "UVR-DeEcho-DeReverb", "htdemucs"].map((model) => (
                        <option key={model} value={model}>{model}</option>
                      ))}
                    </select>
                  </div>
                  <PathPickerRow
                    label="模型"
                    settingKey="uvr_mdx_inst_hq3_path"
                    value={String(settings.uvr_mdx_inst_hq3_path ?? "")}
                    onSave={updateSetting}
                    saving={saving}
                    saved={saved}
                    placeholder="可选：直接指定当前模型文件所在目录"
                    title="选择 UVR 模型文件夹"
                  />
                  <DeviceChoice
                    value={String(settings.uvr_device ?? "cuda")}
                    onChange={(value) => updateSetting("uvr_device", value)}
                  />
                </CardContent>
              </Card>

              {/* Voiceprint */}
              <VoiceprintCard settings={settings} updateSetting={updateSetting} />

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

              {/* VLM — image understanding */}
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="text-base">视觉模型（图文笔记）</CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                  <p className="text-xs text-muted-foreground">OpenAI-Compatible API，用于小红书图文笔记的图片 OCR / 场景描述。</p>
                  <SettingRow
                    label="API Base"
                    settingKey="vlm_api_base"
                    value={String(settings.vlm_api_base ?? "")}
                    onSave={updateSetting}
                    saving={saving}
                    saved={saved}
                    placeholder="http://localhost:8080/v1"
                  />
                  <SettingRow
                    label="API Key"
                    settingKey="vlm_api_key"
                    value={String(settings.vlm_api_key ?? "")}
                    onSave={updateSetting}
                    saving={saving}
                    saved={saved}
                    masked
                  />
                  <SettingRow
                    label="模型"
                    settingKey="vlm_model"
                    value={String(settings.vlm_model ?? "qwen2.5-vl-7b-instruct")}
                    onSave={updateSetting}
                    saving={saving}
                    saved={saved}
                  />
                  <SettingRow
                    label="Max Tokens"
                    settingKey="vlm_max_tokens"
                    value={String(settings.vlm_max_tokens ?? 1024)}
                    onSave={updateSetting}
                    saving={saving}
                    saved={saved}
                  />
                  <SettingRow
                    label="并发数"
                    settingKey="vlm_concurrency"
                    value={String(settings.vlm_concurrency ?? 3)}
                    onSave={updateSetting}
                    saving={saving}
                    saved={saved}
                  />
                </CardContent>
              </Card>
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

          {/* ── Other platforms ── */}
          {activeTab === "xiaoyuzhou" && (
            <PlaceholderSection
              title="小宇宙"
              description="已支持公开单集页面：提取页面元数据、下载 m4a，并转为本地 ASR 使用的 wav。"
              comingSoon={false}
            />
          )}
          {activeTab === "xiaohongshu" && (
            <PlaceholderSection
              title="小红书"
              description="已支持公开视频笔记：解析分享链接/短链、下载 mp4，并提取音频进入本地 ASR。私密或风控笔记可配置 Cookie 后重试。"
              comingSoon={false}
            />
          )}
          {activeTab === "zhihu" && (
            <ZhihuCard settings={settings} updateSetting={updateSetting} saving={saving} saved={saved} />
          )}
        </div>
      </div>
    </div>
  )
}

interface ZhihuCardProps {
  settings: Settings
  updateSetting: (key: string, value: unknown) => Promise<void>
  saving: Record<string, boolean>
  saved: Record<string, boolean>
}

function ZhihuCard({ settings, updateSetting, saving, saved }: ZhihuCardProps) {
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
            onChange={(e) => updateSetting("zhihu_browser_mode", e.target.value)}
            className="h-8 rounded-md border border-input bg-background px-2 text-sm"
          >
            <option value="background">后台最小化</option>
            <option value="foreground">前台可见</option>
          </select>
          {saving.zhihu_browser_mode && <span className="text-xs text-muted-foreground">保存中</span>}
          {saved.zhihu_browser_mode && <HugeiconsIcon icon={Tick02Icon} className="h-3.5 w-3.5 text-green-600" />}
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
      <CardContent className="space-y-4">
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
            <div className="max-w-xl space-y-2">
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

            <div className="max-w-xl space-y-2">
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
    subtitle_engine: string
    subtitle_languages: string
    subtitle_strict_validation: boolean
    subtitle_min_coverage: number
    subtitle_allow_legacy_fallback: boolean
  } | null>(null)

  const [savedQuality, setSavedQuality] = useState(false)
  const [savedSubtitle, setSavedSubtitle] = useState(false)
  const [savingPlatform, setSavingPlatform] = useState<Record<string, boolean>>({})
  const [savedPlatform, setSavedPlatform] = useState<Record<string, boolean>>({})

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

  const updatePlatformSetting = async (key: string, value: unknown) => {
    if (!platformConfig) return
    setPlatformConfig((prev) => prev ? { ...prev, [key]: value } : prev)
    setSavingPlatform((s) => ({ ...s, [key]: true }))
    try {
      await api.platforms.update("bilibili", { [key]: value })
      setSavedPlatform((s) => ({ ...s, [key]: true }))
      setTimeout(() => setSavedPlatform((s) => ({ ...s, [key]: false })), 1500)
    } catch {
      setPlatformConfig(platformConfig)
    } finally {
      setSavingPlatform((s) => ({ ...s, [key]: false }))
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

              <Separator />
              <p className="text-xs font-medium text-muted-foreground">字幕下载与校验</p>

              <div className="flex items-center gap-3">
                <Label className="w-24 shrink-0 text-sm text-muted-foreground">字幕引擎</Label>
                <select
                  value={platformConfig.subtitle_engine}
                  onChange={(e) => updatePlatformSetting("subtitle_engine", e.target.value)}
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
                    onCheckedChange={(v) => updatePlatformSetting("subtitle_strict_validation", Boolean(v))}
                  />
                </div>
              </div>

              <div className="flex items-center gap-3">
                <Label className="w-24 shrink-0 text-sm text-muted-foreground">最低覆盖率</Label>
                <select
                  value={String(platformConfig.subtitle_min_coverage)}
                  onChange={(e) => updatePlatformSetting("subtitle_min_coverage", Number(e.target.value))}
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
                    onCheckedChange={(v) => updatePlatformSetting("subtitle_allow_legacy_fallback", Boolean(v))}
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
        label="模型"
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
