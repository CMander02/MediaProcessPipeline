import { useEffect, useRef, useState, type ReactNode } from "react"
import { HugeiconsIcon } from "@hugeicons/react"
import { Delete01Icon, PlusSignIcon, Tick02Icon } from "@hugeicons/core-free-icons"

import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group"
import { Separator } from "@/components/ui/separator"
import { Slider } from "@/components/ui/slider"
import { Switch } from "@/components/ui/switch"
import type { Settings } from "@/lib/api"
import { DeviceChoice, PathPickerRow, SettingRow } from "./setting-controls"

type UpdateSetting = (key: string, value: unknown) => Promise<void>
type UpdateSettings = (updates: Record<string, unknown>) => Promise<void>

interface SharedSettingsProps {
  settings: Settings
  updateSetting: UpdateSetting
  saving: Record<string, boolean>
  saved: Record<string, boolean>
}

interface RegistrySettingsProps extends SharedSettingsProps {
  visibleLlmProvider: string
  updateSettings: UpdateSettings
}

export function RegistrySettings({
  settings,
  visibleLlmProvider,
  updateSetting,
  updateSettings,
  saving,
  saved,
}: RegistrySettingsProps) {
  const [query, setQuery] = useState("")
  const [selectedId, setSelectedId] = useState("deepseek")

  const entries: ModelListItem[] = [
    {
      id: "deepseek",
      title: "DeepSeek",
      description: "原生 reasoning chat",
      badge: "DS",
      status: visibleLlmProvider === "deepseek" ? "ON" : undefined,
    },
    {
      id: "custom",
      title: "Custom",
      description: "OpenAI-compatible profiles",
      badge: "CU",
      status: visibleLlmProvider === "custom" ? "ON" : undefined,
    },
    {
      id: "openai",
      title: "OpenAI",
      description: "官方 OpenAI API",
      badge: "OA",
      status: visibleLlmProvider === "openai" ? "ON" : undefined,
    },
    {
      id: "anthropic",
      title: "Anthropic",
      description: "Claude API",
      badge: "AI",
      status: visibleLlmProvider === "anthropic" ? "ON" : undefined,
    },
    {
      id: "local",
      title: "本地 HF 模型",
      description: "参数在 Local Models 维护",
      badge: "HF",
      status: visibleLlmProvider === "local" ? "ON" : undefined,
    },
    {
      id: "vision",
      title: "Vision Server",
      description: "图文笔记图片理解",
      badge: "VL",
      status: String(settings.vlm_model ?? "") ? "ON" : undefined,
    },
    {
      id: "purpose",
      title: "Purpose Binding",
      description: "阶段用途绑定",
      badge: "PB",
      status: String(settings.polish_provider ?? "") ? "ON" : undefined,
    },
  ]

  const activeItem = entries.find((entry) => entry.id === selectedId) ?? entries[0]
  const isLlmServer = ["deepseek", "custom", "openai", "anthropic", "local"].includes(activeItem.id)

  return (
    <ModelListLayout
      searchPlaceholder="搜索模型服务..."
      query={query}
      onQueryChange={setQuery}
      items={entries}
      selectedId={activeItem.id}
      onSelect={setSelectedId}
    >
      <DetailHeader
        title={activeItem.title}
        description={activeItem.description}
        active={isLlmServer ? visibleLlmProvider === activeItem.id : undefined}
        onActivate={isLlmServer ? () => updateSetting("llm_provider", activeItem.id) : undefined}
      />

      {activeItem.id === "deepseek" && (
        <DeepSeekConfig
          settings={settings}
          updateSetting={updateSetting}
          saving={saving}
          saved={saved}
        />
      )}

      {activeItem.id === "custom" && (
        <CustomProfilesEditor settings={settings} updateSettings={updateSettings} />
      )}

      {activeItem.id === "openai" && (
        <div className="space-y-3">
          <SettingRow
            label="API Base"
            settingKey="openai_api_base"
            value={String(settings.openai_api_base ?? "")}
            onSave={updateSetting}
            saving={saving}
            saved={saved}
            placeholder="https://api.openai.com/v1"
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
          <SettingRow
            label="模型"
            settingKey="openai_model"
            value={String(settings.openai_model ?? "")}
            onSave={updateSetting}
            saving={saving}
            saved={saved}
          />
        </div>
      )}

      {activeItem.id === "anthropic" && (
        <div className="space-y-3">
          <SettingRow
            label="API Base"
            settingKey="anthropic_api_base"
            value={String(settings.anthropic_api_base ?? "")}
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
          <SettingRow
            label="模型"
            settingKey="anthropic_model"
            value={String(settings.anthropic_model ?? "")}
            onSave={updateSetting}
            saving={saving}
            saved={saved}
          />
        </div>
      )}

      {activeItem.id === "local" && (
        <div className="space-y-3 rounded-lg border border-dashed border-border p-4 text-sm text-muted-foreground">
          <p>本地 HF 模型参数在 Local Models 的 Local LLM Server 中维护。</p>
          <p>打开右上角开关会把全局 LLM provider 切到本地模型。</p>
        </div>
      )}

      {activeItem.id === "vision" && (
        <div className="space-y-3">
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
        </div>
      )}

      {activeItem.id === "purpose" && (
        <div className="space-y-5">
          <div className="space-y-2">
            <Label className="text-sm text-muted-foreground">润色阶段使用</Label>
            <RadioGroup
              value={["", "local", "deepseek", "custom"].includes(String(settings.polish_provider ?? "local")) ? String(settings.polish_provider ?? "local") : ""}
              onValueChange={(value) => updateSetting("polish_provider", value)}
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

          <div className="flex items-center gap-3">
            <Label className="w-24 shrink-0 text-sm text-muted-foreground">润色并发</Label>
            <select
              value={String(settings.llm_polish_concurrency ?? 4)}
              onChange={(event) => updateSetting("llm_polish_concurrency", Number(event.target.value))}
              className="h-8 rounded-md border border-input bg-background px-3 text-sm"
            >
              {[1, 2, 3, 4, 6, 8].map((value) => (
                <option key={value} value={value}>{value}</option>
              ))}
            </select>
            {saved.llm_polish_concurrency && (
              <HugeiconsIcon icon={Tick02Icon} className="h-3.5 w-3.5 text-emerald-500" />
            )}
          </div>
        </div>
      )}
    </ModelListLayout>
  )
}

interface LocalModelSettingsProps extends SharedSettingsProps {
  detectLocalUvr: () => Promise<void>
  uvrDetecting: boolean
  uvrDetection: string | null
}

export function LocalModelSettings({
  settings,
  updateSetting,
  saving,
  saved,
  detectLocalUvr,
  uvrDetecting,
  uvrDetection,
}: LocalModelSettingsProps) {
  const [query, setQuery] = useState("")
  const [selectedId, setSelectedId] = useState("local-llm")
  const entries: ModelListItem[] = [
    {
      id: "local-llm",
      title: "Local LLM Server",
      description: "Hugging Face 文本模型",
      badge: "LL",
      status: settings.llm_provider === "local" ? "ON" : undefined,
    },
    {
      id: "uvr",
      title: "UVR Server",
      description: "人声分离模型",
      badge: "UV",
      status: String(settings.uvr_model ?? "") ? "ON" : undefined,
    },
    {
      id: "voiceprint",
      title: "Voiceprint Purpose",
      description: "说话人归并阈值",
      badge: "VP",
      status: (settings.enable_voiceprint ?? true) ? "ON" : undefined,
    },
  ]
  const activeItem = entries.find((entry) => entry.id === selectedId) ?? entries[0]

  return (
    <ModelListLayout
      searchPlaceholder="搜索本地模型..."
      query={query}
      onQueryChange={setQuery}
      items={entries}
      selectedId={activeItem.id}
      onSelect={setSelectedId}
    >
      <DetailHeader
        title={activeItem.title}
        description={activeItem.description}
        active={activeItem.id === "local-llm" ? settings.llm_provider === "local" : undefined}
        onActivate={activeItem.id === "local-llm" ? () => updateSetting("llm_provider", "local") : undefined}
      />

      {activeItem.id === "local-llm" && (
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
              onChange={(event) => updateSetting("local_llm_dtype", event.target.value)}
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
            onSave={(key, value) => updateSetting(key, Number(value))}
            saving={saving}
            saved={saved}
          />
        </div>
      )}

      {activeItem.id === "uvr" && (
        <div className="space-y-3">
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
              onChange={(event) => updateSetting("uvr_model", event.target.value)}
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
        </div>
      )}

      {activeItem.id === "voiceprint" && (
        <VoiceprintControls settings={settings} updateSetting={updateSetting} />
      )}
    </ModelListLayout>
  )
}

interface ModelListItem {
  id: string
  title: string
  description: string
  badge: string
  status?: string
}

function ModelListLayout({
  searchPlaceholder,
  query,
  onQueryChange,
  items,
  selectedId,
  onSelect,
  children,
}: {
  searchPlaceholder: string
  query: string
  onQueryChange: (value: string) => void
  items: ModelListItem[]
  selectedId: string
  onSelect: (id: string) => void
  children: ReactNode
}) {
  const normalizedQuery = query.trim().toLowerCase()
  const visibleItems = normalizedQuery
    ? items.filter((item) =>
      `${item.title} ${item.description}`.toLowerCase().includes(normalizedQuery),
    )
    : items

  return (
    <div className="grid min-h-[620px] gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
      <aside className="flex min-h-0 flex-col rounded-lg border bg-card/40 p-3">
        <input
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          placeholder={searchPlaceholder}
          className="mb-3 h-9 rounded-full border border-input bg-background px-4 text-sm outline-none transition-colors placeholder:text-muted-foreground focus:border-primary"
        />
        <div className="min-h-0 flex-1 space-y-1 overflow-y-auto">
          {visibleItems.map((item) => {
            const active = item.id === selectedId
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => onSelect(item.id)}
                className={[
                  "flex w-full items-center gap-3 rounded-md px-3 py-2.5 text-left transition-colors",
                  active ? "bg-primary/10 text-foreground" : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
                ].join(" ")}
              >
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">
                  {item.badge}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium">{item.title}</span>
                  <span className="block truncate text-xs text-muted-foreground">{item.description}</span>
                </span>
                {item.status && (
                  <span className="rounded-full border border-emerald-300 bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-700">
                    {item.status}
                  </span>
                )}
              </button>
            )
          })}
        </div>
      </aside>

      <section className="min-w-0 rounded-lg border bg-background">
        <div className="space-y-5 p-5">{children}</div>
      </section>
    </div>
  )
}

function DetailHeader({
  title,
  description,
  active,
  onActivate,
}: {
  title: string
  description: string
  active?: boolean
  onActivate?: () => void | Promise<void>
}) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-border pb-4">
      <div className="space-y-1">
        <h3 className="text-lg font-semibold text-foreground">{title}</h3>
        <p className="text-sm text-muted-foreground">{description}</p>
      </div>
      {typeof active === "boolean" && (
        <div className="flex items-center gap-2">
          <span className="text-xs text-muted-foreground">默认 LLM</span>
          <Switch
            checked={active}
            onCheckedChange={(checked) => {
              if (checked) void onActivate?.()
            }}
          />
        </div>
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
  updateSettings,
}: {
  settings: Settings
  updateSettings: UpdateSettings
}) {
  const profiles = getCustomProfiles(settings)
  const activeId = String(settings.custom_active_profile_id ?? profiles[0]?.id ?? "default")
  const activeProfile = profiles.find((profile) => profile.id === activeId) ?? profiles[0]
  const nextProfileIdRef = useRef(1)

  const saveProfiles = async (next: CustomProfile[], nextActive = activeId) => {
    const active = next.find((profile) => profile.id === nextActive) ?? next[0]
    await updateSettings({
      custom_llm_profiles: next,
      custom_active_profile_id: active.id,
      custom_name: active.name,
      custom_api_base: active.api_base,
      custom_model: active.model,
      custom_api_key: active.api_key,
    })
  }

  const updateProfile = (field: keyof CustomProfile, value: string) => {
    const next = profiles.map((profile) =>
      profile.id === activeProfile.id ? { ...profile, [field]: value } : profile,
    )
    void saveProfiles(next, activeProfile.id)
  }

  const addProfile = () => {
    const profileId = `custom-${nextProfileIdRef.current}`
    nextProfileIdRef.current += 1
    const nextProfile: CustomProfile = {
      id: profileId,
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
          onChange={(event) => void saveProfiles(profiles, event.target.value)}
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

function VoiceprintControls({
  settings,
  updateSetting,
}: {
  settings: Settings
  updateSetting: UpdateSetting
}) {
  const enabled = Boolean(settings.enable_voiceprint ?? true)
  const serverMatch = Number(settings.voiceprint_match_threshold ?? 0.75)
  const serverSuggest = Number(settings.voiceprint_suggest_threshold ?? 0.60)

  const [match, setMatch] = useState(serverMatch)
  const [suggest, setSuggest] = useState(serverSuggest)

  useEffect(() => {
    setMatch(serverMatch)
  }, [serverMatch])
  useEffect(() => {
    setSuggest(serverSuggest)
  }, [serverSuggest])

  const MATCH_MIN = 0.50, MATCH_MAX = 0.90
  const SUGGEST_MIN = 0.40, SUGGEST_MAX = 0.80
  const GAP = 0.10

  const clampSuggest = (nextMatch: number, nextSuggest: number) => {
    const ceiling = Math.min(SUGGEST_MAX, Math.round((nextMatch - GAP) * 100) / 100)
    return Math.max(SUGGEST_MIN, Math.min(ceiling, Math.round(nextSuggest * 100) / 100))
  }

  const handleMatchChange = (value: number) => {
    const rounded = Math.round(value * 100) / 100
    setMatch(rounded)
    const adjusted = clampSuggest(rounded, suggest)
    if (adjusted !== suggest) setSuggest(adjusted)
  }

  const handleSuggestChange = (value: number) => {
    const rounded = Math.round(value * 100) / 100
    const ceiling = Math.min(SUGGEST_MAX, Math.round((match - GAP) * 100) / 100)
    setSuggest(Math.min(rounded, ceiling))
  }

  const commitMatch = () => {
    if (match !== serverMatch) void updateSetting("voiceprint_match_threshold", match)
    const adjusted = clampSuggest(match, suggest)
    if (adjusted !== serverSuggest) void updateSetting("voiceprint_suggest_threshold", adjusted)
  }

  const commitSuggest = () => {
    if (suggest !== serverSuggest) void updateSetting("voiceprint_suggest_threshold", suggest)
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <Label>启用声纹识别</Label>
        <Switch
          checked={enabled}
          onCheckedChange={(value) => updateSetting("enable_voiceprint", Boolean(value))}
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
              onValueChange={(value) => handleMatchChange(value[0])}
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
              onValueChange={(value) => handleSuggestChange(value[0])}
              onValueCommit={commitSuggest}
            />
            <p className="text-xs text-muted-foreground">
              必须 ≤ 匹配阈值 - 0.10。介于此值与匹配阈值之间会建立新身份但记录为可疑匹配。
            </p>
          </div>
        </>
      )}
    </div>
  )
}

type DeepSeekConfigProps = SharedSettingsProps

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
        常用模型名包括 deepseek-v4-flash 和 deepseek-v4-pro。
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
  stage,
  label,
  hint,
  settings,
  updateSetting,
  saving,
  saved,
}: DeepSeekStageBlockProps) {
  const modelKey = `deepseek_${stage}_model`
  const thinkingKey = `deepseek_${stage}_thinking`
  const effortKey = `deepseek_${stage}_effort`

  const thinking = String(settings[thinkingKey] ?? "disabled")
  const effort = String(settings[effortKey] ?? "")

  return (
    <div className="space-y-2 rounded-md border border-border p-3">
      <div className="flex items-baseline justify-between gap-3">
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
          onChange={(event) => updateSetting(thinkingKey, event.target.value)}
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
          onChange={(event) => updateSetting(effortKey, event.target.value)}
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
