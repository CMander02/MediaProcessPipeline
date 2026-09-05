import type { ProviderConfig, ProviderModelRecord, ServiceModelType } from "@/lib/settings-schema"
import { SERVICE_MODEL_TYPES, getEndpointPathForModelType } from "@/lib/settings-model-registry"
import { Button } from "@/components/ui/button"
import { HugeiconsIcon } from "@hugeicons/react"
import { Delete01Icon } from "@hugeicons/core-free-icons"
import { type ProviderModelCatalogResult } from "@/lib/api"
import { type ReactNode } from "react"
import {
  normalizeProviderModelType,
  getProviderCapabilitiesForModelType,
  getProviderDefaultParams,
  getModelCapabilities,
  parseJsonObject,
  getProviderModels,
} from "./registry-utils"
import { ProviderFormRow, ProviderTextInput } from "./controls"

export function ProviderModelItem({
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

export function ProviderAddModelPanel({
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

export function ProviderModelCatalogPanel({
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
