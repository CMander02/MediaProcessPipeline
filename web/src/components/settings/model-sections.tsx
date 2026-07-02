import { useEffect, useRef, useState, type ReactNode } from "react"
import { HugeiconsIcon } from "@hugeicons/react"
import {
  ChevronDownIcon,
  ChevronRightIcon,
  CircleMinusIcon,
  CirclePlusIcon,
  Delete01Icon,
  PlusSignIcon,
} from "@hugeicons/core-free-icons"
import AnthropicMono from "@lobehub/icons/es/Anthropic/components/Mono"
import DeepSeekColor from "@lobehub/icons/es/DeepSeek/components/Color"
import LobeHubColor from "@lobehub/icons/es/LobeHub/components/Color"
import OpenAIMono from "@lobehub/icons/es/OpenAI/components/Mono"
import SiliconCloudColor from "@lobehub/icons/es/SiliconCloud/components/Color"

import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Separator } from "@/components/ui/separator"
import { Slider } from "@/components/ui/slider"
import { Switch } from "@/components/ui/switch"
import { api, type ProviderModelCatalogResult, type Settings } from "@/lib/api"
import {
  SERVICE_MODEL_TYPES,
  getCapabilitiesForModelType,
  getEndpointPathForModelType,
  isServiceModelType,
  normalizeServiceModelType,
} from "@/lib/settings-model-registry"
import type {
  ProviderConfig,
  ProviderModelRecord,
  RuntimeModelBinding,
  ServiceModelRecord,
  ServiceModelType,
} from "@/lib/settings-schema"
import { DeviceChoice, PathPickerRow, SettingRow } from "./setting-controls"

type UpdateSetting = (key: string, value: unknown) => Promise<void>
type UpdateSettings = (updates: Record<string, unknown>) => Promise<void>

interface SharedSettingsProps {
  settings: Settings
  updateSetting: UpdateSetting
  saving: Record<string, boolean>
  saved: Record<string, boolean>
}

interface PurposeModelBindingsProps {
  settings: Settings
  updateSetting: UpdateSetting
}

interface ModelBindingOption {
  value: string
  label: string
}

interface PurposeBindingDef {
  key: string
  label: string
  description: string
  options: ModelBindingOption[]
  fallback: string
}

export function PurposeModelBindings({ settings, updateSetting }: PurposeModelBindingsProps) {
  const textOptions = getProviderModelOptions(settings, "llm")
  const asrOptions = getProviderModelOptions(settings, "asr")
  const visionOptions = getProviderModelOptions(settings, "vision")
  const embeddingOptions = getProviderModelOptions(settings, "embedding")
  const runtimeBindings = getRuntimeModelBindings(settings)

  const bindings: PurposeBindingDef[] = [
    {
      key: "polish",
      label: "字幕简单润色",
      description: "字幕初修、错字修正和轻量断句。",
      options: textOptions,
      fallback: bindingValue(runtimeBindings.polish, "deepseek", settings.deepseek_polish_model, "deepseek-v4-flash"),
    },
    {
      key: "subtitle_refine",
      label: "字幕二次润色",
      description: "上下文一致性、专名统一和语气调整。",
      options: textOptions,
      fallback: bindingValue(runtimeBindings.subtitle_refine, "deepseek", settings.deepseek_polish_model, "deepseek-v4-flash"),
    },
    {
      key: "analyze",
      label: "字幕分析",
      description: "语言、主题、专名和结构线索抽取。",
      options: textOptions,
      fallback: bindingValue(runtimeBindings.analyze, "deepseek", settings.deepseek_analyze_model, "deepseek-v4-flash"),
    },
    {
      key: "summary",
      label: "全文总结",
      description: "README、章节总结和观点归纳。",
      options: textOptions,
      fallback: bindingValue(runtimeBindings.summary, "deepseek", settings.deepseek_summary_model, "deepseek-v4-pro"),
    },
    {
      key: "mindmap",
      label: "思维导图",
      description: "导图 map/reduce 和层级结构生成。",
      options: textOptions,
      fallback: bindingValue(runtimeBindings.mindmap, "deepseek", settings.deepseek_mindmap_model, "deepseek-v4-flash"),
    },
    {
      key: "asr",
      label: "ASR",
      description: "语音识别，可选择本地模型或 ASR API。",
      options: asrOptions,
      fallback: asrBindingValue(settings, runtimeBindings.asr),
    },
    {
      key: "vision",
      label: "图文理解",
      description: "小红书图文笔记的 OCR、图片理解和场景描述。",
      options: visionOptions,
      fallback: bindingValue(runtimeBindings.vision, "custom-vision-default", settings.vlm_model, "Qwen/Qwen3.5-4B"),
    },
    {
      key: "embedding",
      label: "知识库向量",
      description: "任务完成后的字幕、摘要和片段索引。",
      options: embeddingOptions,
      fallback: bindingValue(runtimeBindings.embedding, "custom-embedding-default", settings.kb_embedding_model, "qwen3-embedding-0.6b"),
    },
  ]

  const updateBinding = async (binding: PurposeBindingDef, value: string) => {
    const [providerId, modelId] = splitModelValue(value)
    await updateSetting("runtime_model_bindings", {
      ...runtimeBindings,
      [binding.key]: {
        provider_id: providerId,
        model_id: modelId,
        capability: capabilityForBinding(binding.key),
      },
    })
  }

  return (
    <CardLikeSection title="模型用途">
      <div className="grid gap-3 lg:grid-cols-2">
        {bindings.map((binding) => (
          <PurposeBindingRow
            key={binding.key}
            binding={binding}
            value={binding.fallback}
            onChange={(value) => updateBinding(binding, value)}
          />
        ))}
      </div>
    </CardLikeSection>
  )
}

function PurposeBindingRow({
  binding,
  value,
  onChange,
}: {
  binding: PurposeBindingDef
  value: string
  onChange: (value: string) => Promise<void>
}) {
  const hasSelectedValue = binding.options.some((option) => option.value === value)
  const selectedValue = hasSelectedValue ? value : binding.options[0]?.value ?? ""

  return (
    <div className="space-y-2 rounded-lg border border-border/70 p-3">
      <div className="space-y-1">
        <Label className="text-sm font-medium">{binding.label}</Label>
        <p className="text-xs leading-5 text-muted-foreground">{binding.description}</p>
      </div>
      <select
        value={selectedValue}
        onChange={(event) => void onChange(event.target.value)}
        disabled={binding.options.length === 0}
        className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
      >
        {binding.options.length === 0 ? (
          <option value="">无可用模型</option>
        ) : (
          binding.options.map((option) => (
            <option key={option.value} value={option.value}>{option.label}</option>
          ))
        )}
      </select>
    </div>
  )
}

function CardLikeSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="rounded-lg border bg-card p-4">
      <h3 className="mb-3 text-base font-semibold">{title}</h3>
      {children}
    </div>
  )
}

function modelValue(provider: string, model: unknown, fallback = ""): string {
  const modelId = String(model ?? fallback).trim()
  return modelId ? `${provider}:${modelId}` : `${provider}:`
}

interface RegistrySettingsProps extends SharedSettingsProps {
  visibleLlmProvider: string
  updateSettings: UpdateSettings
}

export function RegistrySettings({
  settings,
  updateSettings,
}: RegistrySettingsProps) {
  const [query, setQuery] = useState("")
  const [selectedId, setSelectedId] = useState("deepseek")
  const [isAddingModelFor, setIsAddingModelFor] = useState("")
  const [newModelId, setNewModelId] = useState("")
  const [newModelType, setNewModelType] = useState<ServiceModelType>("llm")
  const [message, setMessage] = useState<string | null>(null)
  const [modelCatalog, setModelCatalog] = useState<ProviderModelCatalogResult | null>(null)
  const [catalogLoading, setCatalogLoading] = useState(false)

  const providers = getProviders(settings)
  const deletedProviderIds = getDeletedProviderIds(settings)
  const visibleProviders = providers.filter((provider) => providerMatchesFilters(provider, { query }))
  const activeProvider = visibleProviders.find((provider) => provider.id === selectedId)
    ?? visibleProviders[0]
    ?? null
  const providerEntries: ModelListItem[] = visibleProviders.map(providerListItem)

  const saveProviders = (nextProviders: ProviderConfig[]) => updateSettings({ providers: nextProviders })

  const updateProvider = (providerId: string, patch: Partial<ProviderConfig>) => {
    void saveProviders(providers.map((provider) =>
      provider.id === providerId ? normalizeProvider({ ...provider, ...patch }) : provider,
    ))
  }

  const updateModel = (
    providerId: string,
    modelId: string,
    patch: Partial<ProviderModelRecord>,
  ) => {
    void saveProviders(providers.map((provider) => {
      if (provider.id !== providerId) return provider
      const models = getProviderModels(provider).map((model) => {
        if (model.model_id !== modelId) return model
        const next = normalizeProviderModel({ ...model, ...patch }, providerId)
        return next
      })
      return { ...provider, models }
    }))
  }

  const addProvider = () => {
    const nextProvider = createProvider("custom", providers)
    void updateSettings({
      providers: [...providers, nextProvider],
      deleted_provider_ids: deletedProviderIds.filter((id) => id !== nextProvider.id),
    })
    setSelectedId(nextProvider.id)
  }

  const removeProvider = (providerId: string) => {
    const nextProviders = providers.filter((provider) => provider.id !== providerId)
    void updateSettings({
      providers: nextProviders,
      deleted_provider_ids: Array.from(new Set([...deletedProviderIds, providerId])),
    })
    if (selectedId === providerId) setSelectedId(nextProviders[0]?.id ?? "deepseek")
  }

  const addModel = async (providerId: string) => {
    const modelId = newModelId.trim()
    if (!modelId) return
    const metadata = await api.settings.inferProviderModelMetadata({
      model_id: modelId,
      model_type: newModelType,
      provider_id: providerId,
    })
    const nextModel = normalizeProviderModel(metadata, providerId)
    await saveProviders(providers.map((provider) => {
      if (provider.id !== providerId) return provider
      const models = getProviderModels(provider).filter((model) => model.model_id !== modelId)
      return { ...provider, models: [...models, nextModel] }
    }))
    setNewModelId("")
    setNewModelType("llm")
    setIsAddingModelFor("")
  }

  const addCatalogModel = (providerId: string, model: ProviderModelRecord) => {
    void saveProviders(providers.map((provider) => {
      if (provider.id !== providerId) return provider
      const nextModel = normalizeProviderModel(model, providerId)
      const byId = new Map(getProviderModels(provider).map((item) => [item.model_id, item]))
      byId.set(nextModel.model_id, { ...byId.get(nextModel.model_id), ...nextModel, enabled: true })
      return { ...provider, models: Array.from(byId.values()) }
    }))
  }

  const removeModel = (providerId: string, modelId: string) => {
    void saveProviders(providers.map((provider) =>
      provider.id === providerId
        ? { ...provider, models: getProviderModels(provider).filter((model) => model.model_id !== modelId) }
        : provider,
    ))
  }

  const syncModels = async (providerId: string) => {
    setMessage(null)
    try {
      const result = await api.settings.syncProviderModels(providerId)
      await saveProviders(providers.map((provider) =>
        provider.id === providerId ? normalizeProvider(result.provider) : provider,
      ))
      setMessage(`已同步 ${result.models.length} 个模型`)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error))
    }
  }

  const fetchProviderModels = async (providerId: string) => {
    setMessage(null)
    setCatalogLoading(true)
    try {
      const result = await api.settings.fetchProviderModels(providerId)
      setModelCatalog(result)
      setMessage(
        result.error
          ? `已读取配置内可用模型；远端获取失败：${result.error}`
          : `已获取 ${result.models.length} 个模型，当前允许使用 ${result.allowed_models.length} 个`,
      )
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error))
    } finally {
      setCatalogLoading(false)
    }
  }

  const queryBalance = async (providerId: string) => {
    setMessage(null)
    try {
      const result = await api.settings.queryProviderBalance(providerId)
      setMessage(`${providerId} 余额：${JSON.stringify(result.balance)}`)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error))
    }
  }

  return (
    <ModelListLayout
      searchPlaceholder="搜索 Providers..."
      query={query}
      onQueryChange={setQuery}
      items={providerEntries}
      selectedId={activeProvider?.id ?? ""}
      onSelect={setSelectedId}
      footer={(
        <div className="mt-3 space-y-2 border-t border-border pt-3">
          <Button type="button" variant="outline" size="sm" onClick={addProvider} className="h-9 w-full gap-1.5">
            <HugeiconsIcon icon={PlusSignIcon} className="h-3.5 w-3.5" />
            添加 Provider
          </Button>
        </div>
      )}
    >
      {activeProvider ? (
        <ProviderDetailPanel
          provider={activeProvider}
          isAddingModel={isAddingModelFor === activeProvider.id}
          newModelId={newModelId}
          newModelType={newModelType}
          message={message}
          modelCatalog={modelCatalog?.provider_id === activeProvider.id ? modelCatalog : null}
          catalogLoading={catalogLoading}
          onNewModelIdChange={setNewModelId}
          onNewModelTypeChange={setNewModelType}
          onToggleAddModel={() => setIsAddingModelFor((value) => value === activeProvider.id ? "" : activeProvider.id)}
          onAddModel={() => void addModel(activeProvider.id)}
          onAddCatalogModel={(model) => addCatalogModel(activeProvider.id, model)}
          onUpdateProvider={updateProvider}
          onUpdateModel={updateModel}
          onRemoveProvider={removeProvider}
          onRemoveModel={removeModel}
          onFetchModels={() => void fetchProviderModels(activeProvider.id)}
          onSyncModels={() => void syncModels(activeProvider.id)}
          onQueryBalance={() => void queryBalance(activeProvider.id)}
        />
      ) : (
        <ProviderEmptyState hasProviders={providers.length > 0} />
      )}
    </ModelListLayout>
  )
}

function ProviderDetailPanel({
  provider,
  isAddingModel,
  newModelId,
  newModelType,
  message,
  modelCatalog,
  catalogLoading,
  onNewModelIdChange,
  onNewModelTypeChange,
  onToggleAddModel,
  onAddModel,
  onAddCatalogModel,
  onUpdateProvider,
  onUpdateModel,
  onRemoveProvider,
  onRemoveModel,
  onFetchModels,
  onSyncModels,
  onQueryBalance,
}: {
  provider: ProviderConfig
  isAddingModel: boolean
  newModelId: string
  newModelType: ServiceModelType
  message: string | null
  modelCatalog: ProviderModelCatalogResult | null
  catalogLoading: boolean
  onNewModelIdChange: (value: string) => void
  onNewModelTypeChange: (value: ServiceModelType) => void
  onToggleAddModel: () => void
  onAddModel: () => void
  onAddCatalogModel: (model: ProviderModelRecord) => void
  onUpdateProvider: (providerId: string, patch: Partial<ProviderConfig>) => void
  onUpdateModel: (providerId: string, modelId: string, patch: Partial<ProviderModelRecord>) => void
  onRemoveProvider: (providerId: string) => void
  onRemoveModel: (providerId: string, modelId: string) => void
  onFetchModels: () => void
  onSyncModels: () => void
  onQueryBalance: () => void
}) {
  const models = getProviderModels(provider)
  return (
    <div className="space-y-5">
      <DetailHeader
        title={provider.name || provider.id}
        description={`${provider.id} · ${providerTypeLabel(provider)} · ${models.length} models`}
      />

      {message && <p className="rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">{message}</p>}

      <div className="grid gap-3 xl:grid-cols-2">
        <ProviderFormRow label="启用">
          <Switch
            checked={provider.enabled ?? true}
            onCheckedChange={(checked) => onUpdateProvider(provider.id, { enabled: checked })}
            aria-label={`${provider.name || provider.id} 启用`}
          />
        </ProviderFormRow>
        <ProviderFormRow label="类型">
          <select
            value={provider.provider_type || "openai_compatible"}
            onChange={(event) => onUpdateProvider(provider.id, { provider_type: event.target.value })}
            className="h-8 w-full rounded-md border border-input bg-background px-2 text-sm"
          >
            <option value="deepseek">DeepSeek</option>
            <option value="siliconflow">SiliconFlow</option>
            <option value="openai_compatible">OpenAI-compatible</option>
            <option value="anthropic">Anthropic</option>
          </select>
        </ProviderFormRow>
        <ProviderFormRow label="名称">
          <ProviderTextInput
            fieldKey={`${provider.id}-name`}
            value={provider.name || provider.id}
            onCommit={(value) => onUpdateProvider(provider.id, { name: value })}
          />
        </ProviderFormRow>
        <ProviderFormRow label="API Mode">
          <ProviderTextInput
            fieldKey={`${provider.id}-api-mode`}
            value={provider.api_mode || "chat_completions"}
            onCommit={(value) => onUpdateProvider(provider.id, { api_mode: value })}
          />
        </ProviderFormRow>
        <ProviderFormRow label="API Base" className="xl:col-span-2">
          <ProviderTextInput
            fieldKey={`${provider.id}-api-base`}
            value={provider.api_base || ""}
            onCommit={(value) => onUpdateProvider(provider.id, { api_base: value })}
            placeholder="https://api.example.com/v1"
          />
        </ProviderFormRow>
        <ProviderFormRow label="API Key" className="xl:col-span-2">
          <ProviderTextInput
            fieldKey={`${provider.id}-api-key`}
            type="password"
            value={provider.api_key || ""}
            onCommit={(value) => onUpdateProvider(provider.id, { api_key: value })}
            placeholder="API Key"
          />
        </ProviderFormRow>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" variant="outline" size="sm" onClick={onFetchModels} disabled={catalogLoading} className="h-8">
          {catalogLoading ? "获取中..." : "获取模型"}
        </Button>
        <Button type="button" variant="outline" size="sm" onClick={onSyncModels} className="h-8">同步模型</Button>
        <Button type="button" variant="outline" size="sm" onClick={onQueryBalance} className="h-8">查询余额</Button>
        <Button type="button" variant="outline" size="sm" onClick={onToggleAddModel} className="h-8 gap-1.5">
          <HugeiconsIcon icon={PlusSignIcon} className="h-3.5 w-3.5" />
          添加模型
        </Button>
        <Button type="button" variant="ghost" size="sm" onClick={() => onRemoveProvider(provider.id)} className="h-8 text-destructive hover:text-destructive">
          删除 Provider
        </Button>
      </div>

      {modelCatalog && (
        <ProviderModelCatalogPanel
          catalog={modelCatalog}
          provider={provider}
          onAddModel={onAddCatalogModel}
        />
      )}

      {isAddingModel && (
        <ProviderAddModelPanel
          newModelId={newModelId}
          newModelType={newModelType}
          onNewModelIdChange={onNewModelIdChange}
          onNewModelTypeChange={onNewModelTypeChange}
          onAddModel={onAddModel}
        />
      )}

      <section className="space-y-2">
        <div className="flex items-center justify-between gap-3">
          <h4 className="text-sm font-semibold text-foreground">Models</h4>
          <span className="text-xs text-muted-foreground">
            {models.filter((model) => model.enabled !== false).length} enabled / {models.length} total
          </span>
        </div>
        <div className="space-y-2">
          {models.length > 0 ? models.map((model) => (
            <ProviderModelItem
              key={model.id || `${provider.id}:${model.model_id}`}
              provider={provider}
              model={model}
              onUpdateModel={onUpdateModel}
              onRemoveModel={onRemoveModel}
            />
          )) : (
            <div className="rounded-md border border-dashed border-border px-4 py-6 text-center text-sm text-muted-foreground">
              当前 Provider 还没有模型。
            </div>
          )}
        </div>
      </section>
    </div>
  )
}

function ProviderModelItem({
  provider,
  model,
  onUpdateModel,
  onRemoveModel,
}: {
  provider: ProviderConfig
  model: ProviderModelRecord
  onUpdateModel: (providerId: string, modelId: string, patch: Partial<ProviderModelRecord>) => void
  onRemoveModel: (providerId: string, modelId: string) => void
}) {
  const modelType = normalizeProviderModelType(model.model_type)
  return (
    <div className="rounded-md border border-border/80 bg-card/30 p-3">
      <div className="flex items-start gap-3">
        <input
          type="checkbox"
          checked={model.enabled ?? true}
          onChange={(event) => onUpdateModel(provider.id, model.model_id, { enabled: event.target.checked })}
          className="mt-2 h-4 w-4 shrink-0 accent-primary"
          aria-label={`${model.model_id} 启用`}
        />
        <div className="min-w-0 flex-1 space-y-3">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-foreground">{model.display_name || model.model_id}</p>
              <p className="truncate text-xs text-muted-foreground">{model.model_id}</p>
            </div>
            <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">
              {SERVICE_MODEL_TYPES.find((type) => type.value === modelType)?.label ?? modelType}
            </span>
          </div>

          <div className="grid gap-3 xl:grid-cols-2">
            <ProviderFormRow label="模型 ID">
              <ProviderTextInput
                fieldKey={`${provider.id}-${model.model_id}-model-id`}
                value={model.model_id}
                onCommit={(value) => onUpdateModel(provider.id, model.model_id, {
                  id: `${provider.id}:${value}`,
                  model_id: value,
                })}
              />
            </ProviderFormRow>
            <ProviderFormRow label="显示名">
              <ProviderTextInput
                fieldKey={`${provider.id}-${model.model_id}-display-name`}
                value={model.display_name || model.model_id}
                onCommit={(value) => onUpdateModel(provider.id, model.model_id, { display_name: value })}
              />
            </ProviderFormRow>
            <ProviderFormRow label="类型">
              <select
                value={modelType}
                onChange={(event) => {
                  const nextType = event.target.value as ServiceModelType
                  onUpdateModel(provider.id, model.model_id, {
                    model_type: nextType,
                    capabilities: getProviderCapabilitiesForModelType(nextType),
                    endpoint_path: getEndpointPathForModelType(nextType),
                    default_params: getProviderDefaultParams(provider.id, nextType),
                  })
                }}
                className="h-8 w-full rounded-md border border-input bg-background px-2 text-sm"
                aria-label={`${model.model_id} 模型类型`}
              >
                {SERVICE_MODEL_TYPES.map((type) => (
                  <option key={type.value} value={type.value}>{type.label}</option>
                ))}
              </select>
            </ProviderFormRow>
            <ProviderFormRow label="Endpoint">
              <ProviderTextInput
                fieldKey={`${provider.id}-${model.model_id}-endpoint`}
                value={model.endpoint_path || getEndpointPathForModelType(modelType)}
                onCommit={(value) => onUpdateModel(provider.id, model.model_id, { endpoint_path: value })}
              />
            </ProviderFormRow>
            <ProviderFormRow label="能力标签">
              <ProviderTextInput
                fieldKey={`${provider.id}-${model.model_id}-capabilities`}
                value={getModelCapabilities(model).join(",")}
                onCommit={(value) => onUpdateModel(provider.id, model.model_id, {
                  capabilities: value.split(",").map((item) => item.trim()).filter(Boolean),
                })}
              />
            </ProviderFormRow>
            <ProviderFormRow label="默认参数">
              <ProviderTextInput
                fieldKey={`${provider.id}-${model.model_id}-params`}
                value={JSON.stringify(model.default_params ?? {})}
                onCommit={(value) => onUpdateModel(provider.id, model.model_id, {
                  default_params: parseJsonObject(value),
                })}
                className="font-mono text-xs"
              />
            </ProviderFormRow>
          </div>
        </div>
        <Button type="button" variant="ghost" size="icon-sm" onClick={() => onRemoveModel(provider.id, model.model_id)} aria-label={`删除 ${model.model_id}`}>
          <HugeiconsIcon icon={Delete01Icon} className="h-3.5 w-3.5" />
        </Button>
      </div>
    </div>
  )
}

function ProviderAddModelPanel({
  newModelId,
  newModelType,
  onNewModelIdChange,
  onNewModelTypeChange,
  onAddModel,
}: {
  newModelId: string
  newModelType: ServiceModelType
  onNewModelIdChange: (value: string) => void
  onNewModelTypeChange: (value: ServiceModelType) => void
  onAddModel: () => void
}) {
  return (
    <div className="grid gap-2 rounded-md border border-border/70 bg-muted/20 p-3 md:grid-cols-[minmax(0,1fr)_180px_auto]">
      <input
        value={newModelId}
        onChange={(event) => onNewModelIdChange(event.target.value)}
        placeholder="模型 ID，例如 Qwen/Qwen3.5-8B"
        className="h-8 rounded-md border border-input bg-background px-2 text-sm"
      />
      <select
        value={newModelType}
        onChange={(event) => onNewModelTypeChange(event.target.value as ServiceModelType)}
        className="h-8 rounded-md border border-input bg-background px-2 text-sm"
        aria-label="模型类型"
      >
        {SERVICE_MODEL_TYPES.map((type) => (
          <option key={type.value} value={type.value}>{type.label}</option>
        ))}
      </select>
      <Button type="button" size="sm" onClick={onAddModel} className="h-8">保存模型</Button>
    </div>
  )
}

function ProviderModelCatalogPanel({
  catalog,
  provider,
  onAddModel,
}: {
  catalog: ProviderModelCatalogResult
  provider: ProviderConfig
  onAddModel: (model: ProviderModelRecord) => void
}) {
  const configuredIds = new Set(getProviderModels(provider).map((model) => model.model_id))
  const sourceLabel = catalog.source === "remote" ? "远端模型目录" : "配置内模型"

  return (
    <section className="space-y-3 rounded-md border border-border/70 bg-muted/20 p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h4 className="text-sm font-semibold text-foreground">{sourceLabel}</h4>
          <p className="mt-1 text-xs text-muted-foreground">
            共 {catalog.models.length} 个模型，当前允许使用 {catalog.allowed_models.length} 个。
          </p>
        </div>
        {catalog.error && (
          <span className="max-w-md rounded-md bg-background px-2 py-1 text-xs text-muted-foreground">
            {catalog.error}
          </span>
        )}
      </div>

      <div className="grid gap-3 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
        <div className="space-y-2">
          <div className="text-xs font-medium text-muted-foreground">允许使用</div>
          <div className="max-h-64 space-y-1 overflow-y-auto pr-1">
            {catalog.allowed_models.length > 0 ? catalog.allowed_models.map((model) => (
              <ProviderCatalogModelLine key={`${model.model_id}-allowed`} model={model} compact />
            )) : (
              <div className="rounded-md border border-dashed border-border px-3 py-4 text-center text-xs text-muted-foreground">
                当前 provider 没有启用的匹配模型。
              </div>
            )}
          </div>
        </div>

        <div className="space-y-2">
          <div className="text-xs font-medium text-muted-foreground">全部模型</div>
          <div className="max-h-64 space-y-1 overflow-y-auto pr-1">
            {catalog.models.length > 0 ? catalog.models.map((model) => {
              const configured = configuredIds.has(model.model_id)
              return (
                <ProviderCatalogModelLine
                  key={`${model.model_id}-catalog`}
                  model={model}
                  action={configured ? (
                    <span className="text-xs text-muted-foreground">已配置</span>
                  ) : (
                    <Button type="button" variant="ghost" size="sm" onClick={() => onAddModel(model)} className="h-7 px-2">
                      加入配置
                    </Button>
                  )}
                />
              )
            }) : (
              <div className="rounded-md border border-dashed border-border px-3 py-4 text-center text-xs text-muted-foreground">
                没有读取到模型目录。
              </div>
            )}
          </div>
        </div>
      </div>
    </section>
  )
}

function ProviderCatalogModelLine({
  model,
  action,
  compact = false,
}: {
  model: ProviderModelRecord
  action?: ReactNode
  compact?: boolean
}) {
  const modelType = normalizeProviderModelType(model.model_type)
  return (
    <div className="flex items-center gap-2 rounded-md border border-border/60 bg-background px-2 py-1.5">
      <span className="min-w-0 flex-1">
        <span className="block truncate text-xs font-medium text-foreground">{model.display_name || model.model_id}</span>
        {!compact && <span className="block truncate text-[11px] text-muted-foreground">{model.model_id}</span>}
      </span>
      <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
        {SERVICE_MODEL_TYPES.find((type) => type.value === modelType)?.label ?? modelType}
      </span>
      {action}
    </div>
  )
}

function ProviderFormRow({
  label,
  className,
  children,
}: {
  label: string
  className?: string
  children: ReactNode
}) {
  return (
    <div className={["space-y-1", className].filter(Boolean).join(" ")}>
      <Label className="text-xs text-muted-foreground">{label}</Label>
      {children}
    </div>
  )
}

function ProviderTextInput({
  fieldKey,
  value,
  onCommit,
  type = "text",
  placeholder,
  className,
}: {
  fieldKey: string
  value: string
  onCommit: (value: string) => void
  type?: "text" | "password"
  placeholder?: string
  className?: string
}) {
  return (
    <input
      key={fieldKey}
      type={type}
      defaultValue={value}
      onBlur={(event) => onCommit(event.target.value)}
      placeholder={placeholder}
      className={[
        "h-8 w-full rounded-md border border-input bg-background px-2 text-sm",
        className,
      ].filter(Boolean).join(" ")}
    />
  )
}

function ProviderEmptyState({ hasProviders }: { hasProviders: boolean }) {
  return (
    <div className="flex h-full min-h-64 items-center justify-center rounded-md border border-dashed border-border text-sm text-muted-foreground">
      {hasProviders ? "当前筛选条件下没有 Provider。" : "还没有 Provider。"}
    </div>
  )
}

function providerListItem(provider: ProviderConfig): ModelListItem {
  const models = getProviderModels(provider)
  const enabledModels = models.filter((model) => model.enabled !== false).length
  const capabilitySummary = Array.from(new Set(models.flatMap((model) => getModelCapabilities(model))))
    .slice(0, 4)
    .join(" / ")
  return {
    id: provider.id,
    title: provider.name || provider.id,
    description: [
      providerTypeLabel(provider),
      `${models.length} models`,
      capabilitySummary,
    ].filter(Boolean).join(" · "),
    badge: providerBadge(provider),
    icon: providerIcon(provider),
    status: provider.enabled === false ? undefined : `${enabledModels}/${models.length}`,
    searchText: [
      provider.id,
      provider.name,
      provider.provider_type,
      provider.api_base,
      ...models.flatMap((model) => [
        model.model_id,
        model.display_name,
        normalizeProviderModelType(model.model_type),
        getModelCapabilities(model).join(" "),
      ]),
    ].join(" "),
  }
}

function providerIcon(provider: ProviderConfig): ReactNode {
  if (provider.id === "deepseek" || provider.provider_type === "deepseek") return <DeepSeekColor size={18} aria-hidden />
  if (provider.id === "siliconflow" || provider.provider_type === "siliconflow") return <SiliconCloudColor size={18} aria-hidden />
  if (provider.id === "openai") return <OpenAIMono size={18} aria-hidden />
  if (provider.id === "anthropic" || provider.provider_type === "anthropic") return <AnthropicMono size={18} aria-hidden />
  return <LobeHubColor size={18} aria-hidden />
}

function providerBadge(provider: ProviderConfig): string {
  const label = provider.name || provider.id || "Provider"
  return label.slice(0, 2).toUpperCase()
}

function splitModelValue(value: string): [string, string] {
  const index = value.indexOf(":")
  if (index < 0) return ["", value]
  return [value.slice(0, index), value.slice(index + 1)]
}

function capabilityForBinding(key: string): string {
  if (key === "asr") return "asr"
  if (key === "vision") return "vision"
  if (key === "embedding") return "embedding"
  return "llm"
}

function bindingValue(
  binding: RuntimeModelBinding | undefined,
  fallbackProvider: string,
  model: unknown,
  fallbackModel = "",
): string {
  if (binding?.provider_id) {
    return modelValue(binding.provider_id, binding.model_id || model || fallbackModel)
  }
  return modelValue(fallbackProvider, model, fallbackModel)
}

function asrBindingValue(settings: Settings, binding: RuntimeModelBinding | undefined): string {
  const provider = String(settings.asr_provider ?? "qwen3_gguf")
  if (provider === "siliconflow") {
    return bindingValue(binding, "siliconflow", settings.siliconflow_asr_model, "FunAudioLLM/SenseVoiceSmall")
  }
  if (provider === "qwen3") {
    return bindingValue(binding, "qwen3", settings.qwen3_asr_model_path, "Qwen/Qwen3-ASR")
  }
  return bindingValue(
    binding,
    "qwen3_gguf",
    settings.qwen3_gguf_model_path || settings.qwen3_gguf_hf_repo,
    "ggml-org/Qwen3-ASR-1.7B-GGUF:Q8_0",
  )
}

function getRuntimeModelBindings(settings: Settings): Record<string, RuntimeModelBinding> {
  const raw = settings.runtime_model_bindings
  return raw && typeof raw === "object" ? raw : {}
}

function uniqueOptions(options: ModelBindingOption[]): ModelBindingOption[] {
  const seen = new Set<string>()
  return options.filter((option) => {
    if (!option.value || seen.has(option.value)) return false
    seen.add(option.value)
    return true
  })
}

function getProviderModelOptions(settings: Settings, capability: string): ModelBindingOption[] {
  const providers = getProviders(settings)
  const options = providers.flatMap((provider) => {
    if (provider.enabled === false) return []
    return getProviderModels(provider).flatMap((model) => {
      if (model.enabled === false || !modelMatchesCapability(model, capability)) return []
      return [{
        value: modelValue(provider.id, model.model_id),
        label: `${model.display_name || model.model_id} · ${provider.name || provider.id}`,
      }]
    })
  })
  if (capability === "asr") {
    options.unshift({ value: modelValue("qwen3", "Qwen/Qwen3-ASR"), label: "Qwen3-ASR · 本地" })
    options.unshift({
      value: modelValue("qwen3_gguf", String(settings.qwen3_gguf_model_path || settings.qwen3_gguf_hf_repo || "ggml-org/Qwen3-ASR-1.7B-GGUF:Q8_0")),
      label: "Qwen3-ASR GGUF · 本地",
    })
  }
  if (capability === "llm") {
    options.push({ value: modelValue("local", "local-hf"), label: "本地 HF 模型" })
  }
  return uniqueOptions(options)
}

function getProviders(settings: Settings): ProviderConfig[] {
  const providers = Array.isArray(settings.providers)
    ? settings.providers.map((provider) => normalizeProvider(provider))
    : []
  if (providers.length > 0) return providers
  return fallbackProvidersFromLegacy(settings)
}

function getDeletedProviderIds(settings: Settings): string[] {
  const raw = settings.deleted_provider_ids
  return Array.isArray(raw) ? raw.map(String).filter(Boolean) : []
}

function fallbackProvidersFromLegacy(settings: Settings): ProviderConfig[] {
  const serviceModels = Array.isArray(settings.service_models)
    ? settings.service_models.map((model) => normalizeLegacyServiceModel(model))
    : []
  const providers = [
    createProvider("deepseek", []),
    createProvider("siliconflow", []),
    createProvider("custom", []),
  ]
  return providers.map((provider) => {
    if (provider.id === "deepseek") {
      return normalizeProvider({
        ...provider,
        api_base: String(settings.deepseek_api_base ?? "https://api.deepseek.com"),
        api_key: String(settings.deepseek_api_key ?? ""),
        models: [
          providerModel(provider.id, settings.deepseek_polish_model, "llm"),
          providerModel(provider.id, settings.deepseek_summary_model, "llm"),
        ].filter(Boolean) as ProviderModelRecord[],
      })
    }
    if (provider.id === "siliconflow") {
      const models = serviceModels
        .filter((model) => model.connection_id === "siliconflow-asr")
        .map((model) => serviceModelToProviderModel(provider.id, model))
      if (String(settings.siliconflow_asr_model ?? "")) {
        models.push(providerModel(provider.id, settings.siliconflow_asr_model, "asr") as ProviderModelRecord)
      }
      return normalizeProvider({
        ...provider,
        api_base: String(settings.siliconflow_api_base ?? "https://api.siliconflow.cn/v1"),
        api_key: String(settings.siliconflow_api_key ?? ""),
        models,
      })
    }
    return normalizeProvider({
      ...provider,
      id: "custom-default",
      name: String(settings.custom_name ?? "Custom"),
      api_base: String(settings.custom_api_base ?? ""),
      api_key: String(settings.custom_api_key ?? ""),
      models: [providerModel("custom-default", settings.custom_model, "llm")].filter(Boolean) as ProviderModelRecord[],
    })
  })
}

function createProvider(type: string, existing: ProviderConfig[]): ProviderConfig {
  const taken = new Set(existing.map((provider) => provider.id))
  if (type === "deepseek" && !taken.has("deepseek")) {
    return normalizeProvider({
      id: "deepseek",
      name: "DeepSeek",
      provider_type: "deepseek",
      api_base: "https://api.deepseek.com",
      api_key: "",
      enabled: true,
      models: [
        providerModel("deepseek", "deepseek-v4-flash", "llm"),
        providerModel("deepseek", "deepseek-v4-pro", "llm"),
      ].filter(Boolean) as ProviderModelRecord[],
    })
  }
  if (type === "siliconflow" && !taken.has("siliconflow")) {
    return normalizeProvider({
      id: "siliconflow",
      name: "SiliconFlow",
      provider_type: "siliconflow",
      api_base: "https://api.siliconflow.cn/v1",
      api_key: "",
      enabled: true,
      balance: { enabled: true, endpoint_path: "/user/info", method: "GET" },
      models: [
        providerModel("siliconflow", "FunAudioLLM/SenseVoiceSmall", "asr"),
        providerModel("siliconflow", "TeleAI/TeleSpeechASR", "asr"),
        providerModel("siliconflow", "BAAI/bge-reranker-v2-m3", "rerank"),
      ].filter(Boolean) as ProviderModelRecord[],
    })
  }
  const index = existing.filter((provider) => provider.id.startsWith("custom")).length + 1
  return normalizeProvider({
    id: uniqueProviderId(`custom-${index}`, taken),
    name: `Custom ${index}`,
    provider_type: "openai_compatible",
    api_base: "",
    api_key: "",
    enabled: true,
    models: [],
  })
}

function uniqueProviderId(base: string, taken: Set<string>): string {
  if (!taken.has(base)) return base
  let index = 2
  while (taken.has(`${base}-${index}`)) index += 1
  return `${base}-${index}`
}

function normalizeProvider(provider: ProviderConfig): ProviderConfig {
  return {
    ...provider,
    id: String(provider.id || "provider"),
    name: String(provider.name || provider.id || "Provider"),
    provider_type: String(provider.provider_type || "openai_compatible"),
    enabled: provider.enabled ?? true,
    api_base: String(provider.api_base ?? ""),
    api_key: String(provider.api_key ?? ""),
    api_mode: String(provider.api_mode ?? "chat_completions"),
    headers: isRecord(provider.headers) ? provider.headers : {},
    extra_body: isRecord(provider.extra_body) ? provider.extra_body : {},
    balance: isRecord(provider.balance) ? provider.balance : { enabled: false, endpoint_path: "", method: "GET" },
    models: Array.isArray(provider.models)
      ? provider.models.map((model) => normalizeProviderModel(model, String(provider.id || "provider")))
      : [],
  }
}

function getProviderModels(provider: ProviderConfig): ProviderModelRecord[] {
  return Array.isArray(provider.models)
    ? provider.models.map((model) => normalizeProviderModel(model, provider.id))
    : []
}

function normalizeProviderModel(model: ProviderModelRecord, providerId: string): ProviderModelRecord {
  const modelType = normalizeProviderModelType(model.model_type)
  const modelId = String(model.model_id || model.id || "").trim()
  const defaultParams = isRecord(model.default_params) ? model.default_params : {}
  return {
    ...model,
    id: String(model.id || `${providerId}:${modelId}`),
    model_id: modelId,
    display_name: String(model.display_name || modelId),
    enabled: model.enabled ?? true,
    model_type: modelType,
    capabilities: getModelCapabilities({ ...model, model_type: modelType }),
    endpoint_path: String(model.endpoint_path || getEndpointPathForModelType(modelType)),
    default_params: { ...getProviderDefaultParams(providerId, modelType), ...defaultParams },
  }
}

function normalizeProviderModelType(value: unknown): ServiceModelType {
  const normalized = String(value ?? "").trim().toLowerCase()
  return isServiceModelType(normalized) ? normalized : "llm"
}

function getProviderCapabilitiesForModelType(modelType: ServiceModelType): string[] {
  if (modelType === "llm") return ["llm", "chat", "json"]
  if (modelType === "vlm") return ["vlm", "chat", "vision", "json"]
  return getCapabilitiesForModelType(modelType)
}

function getModelCapabilities(model: Pick<ProviderModelRecord, "model_type" | "capabilities">): string[] {
  const modelType = normalizeProviderModelType(model.model_type)
  const current = Array.isArray(model.capabilities) ? model.capabilities.map(String).filter(Boolean) : []
  return Array.from(new Set([...getProviderCapabilitiesForModelType(modelType), ...current]))
}

function modelMatchesCapability(model: ProviderModelRecord, capability: string): boolean {
  if (!capability) return true
  const caps = new Set(getModelCapabilities(model).map((item) => item.toLowerCase()))
  const modelType = normalizeProviderModelType(model.model_type)
  if (capability === "vision") return caps.has("vision") || modelType === "vlm"
  if (capability === "llm") return caps.has("llm") || caps.has("chat") || modelType === "llm"
  return caps.has(capability) || modelType === capability
}

function providerMatchesFilters(
  provider: ProviderConfig,
  filters: { query: string },
): boolean {
  const normalizedQuery = filters.query.trim().toLowerCase()
  const models = getProviderModels(provider)
  if (!normalizedQuery) return true
  return [
    provider.id,
    provider.name,
    provider.provider_type,
    provider.api_base,
    ...models.flatMap((model) => [model.model_id, model.display_name, getModelCapabilities(model).join(" ")]),
  ].join(" ").toLowerCase().includes(normalizedQuery)
}

function providerTypeLabel(provider: ProviderConfig): string {
  const type = String(provider.provider_type || "")
  if (type === "deepseek") return "DeepSeek"
  if (type === "siliconflow") return "SiliconFlow"
  if (type === "anthropic") return "Anthropic"
  return "OpenAI-compatible"
}

function providerModel(providerId: string, model: unknown, modelType: ServiceModelType): ProviderModelRecord | null {
  const modelId = String(model ?? "").trim()
  if (!modelId) return null
  return normalizeProviderModel({
    id: `${providerId}:${modelId}`,
    model_id: modelId,
    display_name: modelId,
    model_type: modelType,
    enabled: true,
    capabilities: getProviderCapabilitiesForModelType(modelType),
    endpoint_path: getEndpointPathForModelType(modelType),
    default_params: getProviderDefaultParams(providerId, modelType),
  }, providerId)
}

function getProviderDefaultParams(providerId: string, modelType: ServiceModelType): Record<string, unknown> {
  if (providerId === "siliconflow" && modelType === "asr") {
    return {
      request_format: "multipart",
      file_field: "file",
      model_field: "model",
      include_language: false,
      max_file_mb: 50,
      max_duration_sec: 3600,
    }
  }
  if (providerId === "siliconflow" && modelType === "rerank") {
    return {
      request_format: "json",
      query_field: "query",
      documents_field: "documents",
      return_documents: false,
      max_chunks_per_doc: 1024,
    }
  }
  return {}
}

function normalizeLegacyServiceModel(model: ServiceModelRecord): ServiceModelRecord {
  const modelType = normalizeServiceModelType(model)
  const modelId = String(model.model_id || model.display_name || "").trim()
  return {
    ...model,
    id: String(model.id || `${model.connection_id}:${modelId}`),
    connection_id: String(model.connection_id || ""),
    model_id: modelId,
    display_name: String(model.display_name || modelId),
    model_type: modelType,
    capabilities: getCapabilitiesForModelType(modelType),
    endpoint_path: getEndpointPathForModelType(modelType),
    enabled: model.enabled ?? true,
    default_params: model.default_params ?? {},
  }
}

function serviceModelToProviderModel(providerId: string, model: ServiceModelRecord): ProviderModelRecord {
  return normalizeProviderModel({
    id: `${providerId}:${model.model_id}`,
    model_id: model.model_id,
    display_name: model.display_name,
    model_type: model.model_type,
    capabilities: model.capabilities,
    endpoint_path: model.endpoint_path,
    enabled: model.enabled,
    default_params: model.default_params,
  }, providerId)
}

function parseJsonObject(value: string): Record<string, unknown> {
  try {
    const parsed = JSON.parse(value)
    return isRecord(parsed) ? parsed : {}
  } catch {
    return {}
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
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
  const [selectedId, setSelectedId] = useState("qwen3-gguf")
  const entries: ModelListItem[] = [
    {
      id: "qwen3-gguf",
      title: "Qwen3-ASR GGUF",
      description: "llama.cpp 本地语音识别",
      badge: "GG",
      status: String(settings.asr_provider ?? "qwen3_gguf") === "qwen3_gguf" ? "ON" : undefined,
    },
    {
      id: "qwen3-asr",
      title: "Qwen3-ASR",
      description: "Torch 兼容语音识别",
      badge: "QA",
      status: String(settings.asr_provider ?? "") === "qwen3" ? "ON" : undefined,
    },
    {
      id: "diarization",
      title: "Diarization",
      description: "说话人分离与归并",
      badge: "SP",
      status: (settings.enable_diarization ?? true) ? "ON" : undefined,
    },
    {
      id: "uvr",
      title: "UVR Server",
      description: "人声分离模型",
      badge: "UV",
      status: String(settings.uvr_model ?? "") ? "ON" : undefined,
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
      />

      {activeItem.id === "qwen3-gguf" && (
        <div className="space-y-3">
          <div className="flex items-center gap-3">
            <Label className="w-24 shrink-0 text-sm text-muted-foreground">Provider</Label>
            <Button
              size="sm"
              variant={String(settings.asr_provider ?? "qwen3_gguf") === "qwen3_gguf" ? "default" : "outline"}
              onClick={() => updateSetting("asr_provider", "qwen3_gguf")}
              className="h-8"
            >
              设为本地 ASR
            </Button>
          </div>
          <SettingRow
            label="llama.cpp"
            settingKey="llama_cpp_binary_path"
            value={String(settings.llama_cpp_binary_path ?? "")}
            onSave={updateSetting}
            saving={saving}
            saved={saved}
            placeholder="留空自动查找 llama-server / llama"
          />
          <SettingRow
            label="模型"
            settingKey="qwen3_gguf_model_path"
            value={String(settings.qwen3_gguf_model_path ?? "")}
            onSave={updateSetting}
            saving={saving}
            saved={saved}
            placeholder="可选：Qwen3-ASR-1.7B-Q8_0.gguf"
          />
          <SettingRow
            label="mmproj"
            settingKey="qwen3_gguf_mmproj_path"
            value={String(settings.qwen3_gguf_mmproj_path ?? "")}
            onSave={updateSetting}
            saving={saving}
            saved={saved}
            placeholder="可选：mmproj-Qwen3-ASR-1.7B-Q8_0.gguf"
          />
          <SettingRow
            label="HF Repo"
            settingKey="qwen3_gguf_hf_repo"
            value={String(settings.qwen3_gguf_hf_repo ?? "ggml-org/Qwen3-ASR-1.7B-GGUF:Q8_0")}
            onSave={updateSetting}
            saving={saving}
            saved={saved}
            placeholder="ggml-org/Qwen3-ASR-1.7B-GGUF:Q8_0"
          />
          <DeviceChoice
            value={String(settings.qwen3_gguf_device ?? "auto")}
            options={["auto", "cuda", "cpu"]}
            onChange={(value) => updateSetting("qwen3_gguf_device", value)}
          />
          <div className="flex items-center gap-3">
            <Label className="w-24 shrink-0 text-sm text-muted-foreground">切块</Label>
            <select
              value={String(settings.qwen3_gguf_chunk_strategy ?? "silero_onnx")}
              onChange={(event) => updateSetting("qwen3_gguf_chunk_strategy", event.target.value)}
              className="h-8 min-w-52 rounded-md border border-input bg-background px-3 text-sm"
            >
              <option value="silero_onnx">Silero ONNX</option>
              <option value="silero_torch">Silero Torch</option>
              <option value="ffmpeg">ffmpeg 固定切块</option>
            </select>
          </div>
          <SettingRow
            label="Silero"
            settingKey="silero_onnx_model_path"
            value={String(settings.silero_onnx_model_path ?? "")}
            onSave={updateSetting}
            saving={saving}
            saved={saved}
            placeholder="可选：silero_vad.onnx"
          />
          <SettingRow
            label="Context"
            settingKey="qwen3_gguf_ctx"
            value={String(settings.qwen3_gguf_ctx ?? 4096)}
            onSave={(key, value) => updateSetting(key, Number(value))}
            saving={saving}
            saved={saved}
          />
          <SettingRow
            label="GPU 层"
            settingKey="qwen3_gguf_n_gpu_layers"
            value={String(settings.qwen3_gguf_n_gpu_layers ?? 99)}
            onSave={(key, value) => updateSetting(key, Number(value))}
            saving={saving}
            saved={saved}
          />
          <SettingRow
            label="超时"
            settingKey="qwen3_gguf_timeout_sec"
            value={String(settings.qwen3_gguf_timeout_sec ?? 300)}
            onSave={(key, value) => updateSetting(key, Number(value))}
            saving={saving}
            saved={saved}
          />
          <SettingRow
            label="保活"
            settingKey="qwen3_gguf_keepalive_sec"
            value={String(settings.qwen3_gguf_keepalive_sec ?? 300)}
            onSave={(key, value) => updateSetting(key, Number(value))}
            saving={saving}
            saved={saved}
          />
        </div>
      )}

      {activeItem.id === "qwen3-asr" && (
        <div className="space-y-3">
          <div className="flex items-center gap-3">
            <Label className="w-24 shrink-0 text-sm text-muted-foreground">Provider</Label>
            <Button
              size="sm"
              variant={String(settings.asr_provider ?? "") === "qwen3" ? "default" : "outline"}
              onClick={() => updateSetting("asr_provider", "qwen3")}
              className="h-8"
            >
              设为本地 ASR
            </Button>
          </div>
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
        </div>
      )}

      {activeItem.id === "diarization" && (
        <DiarizationControls
          settings={settings}
          updateSetting={updateSetting}
          saving={saving}
          saved={saved}
        />
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
          <DeviceChoice
            value={String(settings.uvr_device ?? "cuda")}
            onChange={(value) => updateSetting("uvr_device", value)}
          />
        </div>
      )}

    </ModelListLayout>
  )
}

interface ModelListItem {
  id: string
  title: string
  description: string
  badge: string
  icon?: ReactNode
  status?: string
  searchText?: string
}

function ModelListLayout({
  searchPlaceholder,
  query,
  onQueryChange,
  items,
  selectedId,
  onSelect,
  footer,
  children,
}: {
  searchPlaceholder: string
  query: string
  onQueryChange: (value: string) => void
  items: ModelListItem[]
  selectedId: string
  onSelect: (id: string) => void
  footer?: ReactNode
  children: ReactNode
}) {
  const normalizedQuery = query.trim().toLowerCase()
  const visibleItems = normalizedQuery
    ? items.filter((item) =>
      `${item.title} ${item.description} ${item.searchText ?? ""}`.toLowerCase().includes(normalizedQuery),
    )
    : items

  return (
    <div className="grid h-full min-h-0 grid-cols-[minmax(280px,320px)_minmax(0,1fr)] gap-4 overflow-hidden">
      <aside className="flex min-h-0 flex-col overflow-hidden rounded-lg border bg-card/40 p-3">
        <input
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          placeholder={searchPlaceholder}
          className="mb-3 h-9 rounded-full border border-input bg-background px-4 text-sm outline-none transition-colors placeholder:text-muted-foreground focus:border-primary"
        />
        <div className="min-h-0 flex-1 space-y-1 overflow-y-auto pr-1">
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
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-background text-xs font-semibold text-primary ring-1 ring-border [&_svg]:h-5 [&_svg]:w-5">
                  {item.icon ?? item.badge}
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
        {footer}
      </aside>

      <section className="min-h-0 min-w-0 overflow-hidden rounded-lg border bg-background">
        <div className="h-full space-y-5 overflow-y-auto p-5 pr-4">{children}</div>
      </section>
    </div>
  )
}

function DetailHeader({
  title,
  description,
  active,
  activeLabel = "默认 LLM",
  onActivate,
}: {
  title: string
  description: string
  active?: boolean
  activeLabel?: string
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
          <span className="text-xs text-muted-foreground">{activeLabel}</span>
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

function DiarizationControls({
  settings,
  updateSetting,
  saving,
  saved,
}: SharedSettingsProps) {
  const enabled = Boolean(settings.enable_diarization ?? true)

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <Label>说话人分离</Label>
          <p className="mt-0.5 text-xs text-muted-foreground">
            使用 pyannote 给字幕段落标注 SPEAKER_XX，并为声纹识别提供切片。
          </p>
        </div>
        <Switch
          checked={enabled}
          onCheckedChange={(value) => updateSetting("enable_diarization", Boolean(value))}
        />
      </div>

      {enabled && (
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
            onSave={(key, value) => updateSetting(key, Number(value))}
            saving={saving}
            saved={saved}
          />
        </div>
      )}

      <Separator />
      <VoiceprintControls settings={settings} updateSetting={updateSetting} />
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
    </div>
  )
}
