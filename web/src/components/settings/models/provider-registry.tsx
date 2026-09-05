import { useRef, useState, type ReactNode } from "react"
import type { ProviderConfig, ProviderModelRecord, ServiceModelType } from "@/lib/settings-schema"
import { api, type ProviderModelCatalogResult, type ProviderOAuthStatus } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { HugeiconsIcon } from "@hugeicons/react"
import { PlusSignIcon } from "@hugeicons/core-free-icons"
import { Switch } from "@/components/ui/switch"
import DeepSeekColor from "@lobehub/icons/es/DeepSeek/components/Color"
import SiliconCloudColor from "@lobehub/icons/es/SiliconCloud/components/Color"
import OpenAIMono from "@lobehub/icons/es/OpenAI/components/Mono"
import GeminiColor from "@lobehub/icons/es/Gemini/components/Color"
import AnthropicMono from "@lobehub/icons/es/Anthropic/components/Mono"
import LobeHubColor from "@lobehub/icons/es/LobeHub/components/Color"
import { type RegistrySettingsProps, type ModelListItem } from "./types"
import {
  getProviders,
  getDeletedProviderIds,
  providerMatchesFilters,
  normalizeProvider,
  getProviderModels,
  normalizeProviderModel,
  createProvider,
  isOAuthProvider,
  providerTypeLabel,
  getModelCapabilities,
  normalizeProviderModelType,
} from "./registry-utils"
import {
  ModelListLayout,
  ProviderEmptyState,
  DetailHeader,
  ProviderFormRow,
  ProviderTextInput,
} from "./controls"
import {
  ProviderModelCatalogPanel,
  ProviderAddModelPanel,
  ProviderModelItem,
} from "./provider-model-editor"

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
  const [oauthStatus, setOauthStatus] = useState<{ providerId: string; status: ProviderOAuthStatus } | null>(null)
  const [oauthLoadingProviderId, setOauthLoadingProviderId] = useState("")
  const pendingProviderSave = useRef<Promise<void>>(Promise.resolve())

  const providers = getProviders(settings)
  const deletedProviderIds = getDeletedProviderIds(settings)
  const visibleProviders = providers.filter((provider) => providerMatchesFilters(provider, { query }))
  const activeProvider = visibleProviders.find((provider) => provider.id === selectedId)
    ?? visibleProviders[0]
    ?? null
  const providerEntries: ModelListItem[] = visibleProviders.map(providerListItem)

  const saveProviders = (nextProviders: ProviderConfig[]) => {
    const operation = updateSettings({ providers: nextProviders })
    pendingProviderSave.current = operation
    return operation
  }

  const updateProvider = (providerId: string, patch: Partial<ProviderConfig>) => {
    if ("provider_type" in patch || "cli_path" in patch || "timeout_sec" in patch) {
      setOauthStatus((current) => current?.providerId === providerId ? null : current)
    }
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
      await pendingProviderSave.current
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
      await pendingProviderSave.current
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

  const checkOAuthStatus = async (providerId: string) => {
    setMessage(null)
    setOauthStatus((current) => current?.providerId === providerId ? null : current)
    setOauthLoadingProviderId(providerId)
    try {
      await pendingProviderSave.current
      const status = await api.settings.providerOAuthStatus(providerId)
      const expectedType = providers.find((provider) => provider.id === providerId)?.provider_type
      if (expectedType && status.provider_type !== expectedType) {
        throw new Error("OAuth 检测结果与当前 Provider 类型不一致，请重试。")
      }
      setOauthStatus({ providerId, status })
      setMessage(status.message)
    } catch (error) {
      setOauthStatus((current) => current?.providerId === providerId ? null : current)
      setMessage(error instanceof Error ? error.message : String(error))
    } finally {
      setOauthLoadingProviderId((current) => current === providerId ? "" : current)
    }
  }

  return (
    <ModelListLayout
      searchPlaceholder="搜索 Providers..."
      query={query}
      onQueryChange={setQuery}
      items={providerEntries}
      selectedId={activeProvider?.id ?? ""}
      onSelect={(providerId) => {
        setSelectedId(providerId)
        setMessage(null)
      }}
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
          oauthStatus={oauthStatus?.providerId === activeProvider.id ? oauthStatus.status : null}
          oauthLoading={oauthLoadingProviderId === activeProvider.id}
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
          onCheckOAuthStatus={() => void checkOAuthStatus(activeProvider.id)}
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
  oauthStatus,
  oauthLoading,
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
  onCheckOAuthStatus,
}: {
  provider: ProviderConfig
  isAddingModel: boolean
  newModelId: string
  newModelType: ServiceModelType
  message: string | null
  modelCatalog: ProviderModelCatalogResult | null
  catalogLoading: boolean
  oauthStatus: ProviderOAuthStatus | null
  oauthLoading: boolean
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
  onCheckOAuthStatus: () => void
}) {
  const models = getProviderModels(provider)
  const oauthProvider = isOAuthProvider(provider)
  return (
    <div className="space-y-5">
      <DetailHeader
        title={provider.name || provider.id}
        description={`${provider.id} · ${providerTypeLabel(provider)} · ${models.length} models`}
      />

      {message && <p role="status" aria-live="polite" className="rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">{message}</p>}

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
            aria-label="Provider 类型"
            value={provider.provider_type || "openai_compatible"}
            onChange={(event) => onUpdateProvider(provider.id, { provider_type: event.target.value })}
            className="h-8 w-full rounded-md border border-input bg-background px-2 text-sm"
          >
            <option value="deepseek">DeepSeek</option>
            <option value="siliconflow">SiliconFlow</option>
            <option value="openai_compatible">OpenAI-compatible</option>
            <option value="anthropic">Anthropic</option>
            <option value="codex_oauth">Codex OAuth</option>
            <option value="agy_oauth">Antigravity OAuth</option>
          </select>
        </ProviderFormRow>
        <ProviderFormRow label="名称">
          <ProviderTextInput
            fieldKey={`${provider.id}-name`}
            value={provider.name || provider.id}
            onCommit={(value) => onUpdateProvider(provider.id, { name: value })}
          />
        </ProviderFormRow>
        {oauthProvider ? (
          <>
            <ProviderFormRow label="CLI 路径" className="xl:col-span-2">
              <ProviderTextInput
                fieldKey={`${provider.id}-cli-path`}
                value={provider.cli_path || ""}
                onCommit={(value) => onUpdateProvider(provider.id, { cli_path: value })}
                placeholder="留空时自动查找本机 CLI"
                ariaLabel="CLI 路径"
              />
            </ProviderFormRow>
            <ProviderFormRow label="推理超时（秒）">
              <ProviderTextInput
                fieldKey={`${provider.id}-timeout-sec`}
                type="number"
                value={String(provider.timeout_sec || 600)}
                onCommit={(value) => onUpdateProvider(provider.id, { timeout_sec: Math.max(1, Math.trunc(Number(value) || 600)) })}
                ariaLabel="推理超时（秒）"
              />
            </ProviderFormRow>
            <ProviderFormRow label="OAuth 登录">
              <div className="flex flex-wrap items-center gap-2">
                <span aria-live="polite" className={[
                  "rounded-full px-2 py-0.5 text-xs",
                  oauthStatus?.authenticated
                    ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
                    : "bg-muted text-muted-foreground",
                ].join(" ")}>
                  {oauthStatus
                    ? (oauthStatus.authenticated ? "已连接" : (oauthStatus.installed ? "等待登录" : "CLI 未安装"))
                    : "等待检测"}
                </span>
                <Button type="button" variant="outline" size="sm" onClick={onCheckOAuthStatus} disabled={oauthLoading} className="h-8">
                  {oauthLoading ? "检测中..." : "检测 OAuth"}
                </Button>
              </div>
            </ProviderFormRow>
            <p className="text-xs leading-5 text-muted-foreground xl:col-span-2">
              复用本机 CLI 的 OAuth 登录与 Coding Plan 配额，凭据由 CLI 自己保存和刷新。
              {provider.provider_type === "agy_oauth" ? " Antigravity CLI 会在本机保留会话与日志。" : " Codex 调用使用临时会话，不写入会话历史。"}
              {oauthStatus?.current_model ? ` 当前模型：${oauthStatus.current_model}` : ""}
            </p>
          </>
        ) : (
          <>
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
          </>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" variant="outline" size="sm" onClick={onFetchModels} disabled={catalogLoading} className="h-8">
          {catalogLoading ? "获取中..." : "获取模型"}
        </Button>
        <Button type="button" variant="outline" size="sm" onClick={onSyncModels} className="h-8">同步模型</Button>
        {provider.balance?.enabled && (
          <Button type="button" variant="outline" size="sm" onClick={onQueryBalance} className="h-8">查询余额</Button>
        )}
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
  if (provider.id === "openai" || provider.provider_type === "codex_oauth") return <OpenAIMono size={18} aria-hidden />
  if (provider.provider_type === "agy_oauth") return <GeminiColor size={18} aria-hidden />
  if (provider.id === "anthropic" || provider.provider_type === "anthropic") return <AnthropicMono size={18} aria-hidden />
  return <LobeHubColor size={18} aria-hidden />
}

function providerBadge(provider: ProviderConfig): string {
  const label = provider.name || provider.id || "Provider"
  return label.slice(0, 2).toUpperCase()
}
