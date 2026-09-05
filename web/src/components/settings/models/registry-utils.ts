import type { ProviderConfig, ProviderModelRecord, RuntimeModelBinding, ServiceModelRecord, ServiceModelType } from "@/lib/settings-schema"
import { type Settings } from "@/lib/api"
import {
  getCapabilitiesForModelType,
  getEndpointPathForModelType,
  isServiceModelType,
  normalizeServiceModelType,
} from "@/lib/settings-model-registry"
import { type ModelBindingOption, type CustomProfile } from "./types"

function modelValue(provider: string, model: unknown, fallback = ""): string {
  const modelId = String(model ?? fallback).trim()
  return modelId ? `${provider}:${modelId}` : `${provider}:`
}

export function splitModelValue(value: string): [string, string] {
  const index = value.indexOf(":")
  if (index < 0) return ["", value]
  return [value.slice(0, index), value.slice(index + 1)]
}

export function capabilityForBinding(key: string): string {
  if (key === "asr") return "asr"
  if (key === "vision") return "vision"
  if (key === "embedding") return "embedding"
  return "llm"
}

export function bindingValue(
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

export function asrBindingValue(settings: Settings, binding: RuntimeModelBinding | undefined): string {
  const provider = String(settings.asr_provider ?? "sherpa_onnx")
  if (provider === "siliconflow") {
    return bindingValue(binding, "siliconflow", settings.siliconflow_asr_model, "FunAudioLLM/SenseVoiceSmall")
  }
  return bindingValue(
    binding,
    "sherpa_onnx",
    settings.sherpa_model_id,
    "sensevoice-small-int8",
  )
}

export function getRuntimeModelBindings(settings: Settings): Record<string, RuntimeModelBinding> {
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

export function getProviderModelOptions(settings: Settings, capability: string): ModelBindingOption[] {
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
    const localModels = [
      ["qwen3-asr-1.7b-onnx", "Qwen3-ASR 1.7B INT8"],
      ["sensevoice-small-int8", "SenseVoice Small INT8"],
      ["paraformer-zh-int8", "Paraformer Chinese INT8"],
      ["whisper-small-multi-int8", "Whisper Small Multilingual INT8"],
    ]
    options.unshift(...localModels.map(([id, label]) => ({
      value: modelValue("sherpa_onnx", id),
      label: `${label} · sherpa-onnx`,
    })))
  }
  if (capability === "llm") {
    const name = String(settings.local_llm_name || "Local LLM")
    options.push({ value: modelValue("local", name), label: `${name} · 本地` })
  }
  if (
    capability === "vision"
    && settings.local_llm_engine === "llama_cpp"
    && settings.local_llm_model_path
    && settings.local_llm_mmproj_path
  ) {
    const name = String(settings.local_llm_name || "Local LLM")
    options.push({ value: modelValue("local", name), label: `${name} · 本地多模态` })
  }
  return uniqueOptions(options)
}

export function getProviders(settings: Settings): ProviderConfig[] {
  const providers = Array.isArray(settings.providers)
    ? settings.providers.map((provider) => normalizeProvider(provider))
    : []
  if (providers.length > 0) return providers
  return fallbackProvidersFromLegacy(settings)
}

export function getDeletedProviderIds(settings: Settings): string[] {
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
  const legacyProviders = providers.map((provider) => {
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
  const codex = createProvider("codex_oauth", legacyProviders)
  const agy = createProvider("agy_oauth", [...legacyProviders, codex])
  return [...legacyProviders, codex, agy]
}

export function createProvider(type: string, existing: ProviderConfig[]): ProviderConfig {
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
  if (type === "codex_oauth" && !taken.has("codex-oauth")) {
    return normalizeProvider({
      id: "codex-oauth",
      name: "Codex OAuth",
      provider_type: "codex_oauth",
      api_mode: "oauth_cli",
      cli_path: "",
      timeout_sec: 600,
      enabled: true,
      models: [{
        id: "codex-oauth:default",
        model_id: "default",
        display_name: "CLI 当前默认模型",
        model_type: "llm",
        enabled: true,
        capabilities: ["llm", "chat", "json", "reasoning"],
        endpoint_path: "/chat/completions",
        default_params: {},
      }],
    })
  }
  if (type === "agy_oauth" && !taken.has("agy-oauth")) {
    return normalizeProvider({
      id: "agy-oauth",
      name: "Antigravity OAuth",
      provider_type: "agy_oauth",
      api_mode: "oauth_cli",
      cli_path: "",
      timeout_sec: 600,
      enabled: true,
      models: [{
        id: "agy-oauth:default",
        model_id: "default",
        display_name: "CLI 当前默认模型",
        model_type: "llm",
        enabled: true,
        capabilities: ["llm", "chat", "json", "reasoning"],
        endpoint_path: "/chat/completions",
        default_params: {},
      }],
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

export function normalizeProvider(provider: ProviderConfig): ProviderConfig {
  return {
    ...provider,
    id: String(provider.id || "provider"),
    name: String(provider.name || provider.id || "Provider"),
    provider_type: String(provider.provider_type || "openai_compatible"),
    enabled: provider.enabled ?? true,
    api_base: String(provider.api_base ?? ""),
    api_key: String(provider.api_key ?? ""),
    api_mode: String(provider.api_mode ?? "chat_completions"),
    cli_path: String(provider.cli_path ?? ""),
    timeout_sec: Math.max(1, Number(provider.timeout_sec) || 600),
    headers: isRecord(provider.headers) ? provider.headers : {},
    extra_body: isRecord(provider.extra_body) ? provider.extra_body : {},
    balance: isRecord(provider.balance) ? provider.balance : { enabled: false, endpoint_path: "", method: "GET" },
    models: Array.isArray(provider.models)
      ? provider.models.map((model) => normalizeProviderModel(model, String(provider.id || "provider")))
      : [],
  }
}

export function getProviderModels(provider: ProviderConfig): ProviderModelRecord[] {
  return Array.isArray(provider.models)
    ? provider.models.map((model) => normalizeProviderModel(model, provider.id))
    : []
}

export function normalizeProviderModel(model: ProviderModelRecord, providerId: string): ProviderModelRecord {
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

export function normalizeProviderModelType(value: unknown): ServiceModelType {
  const normalized = String(value ?? "").trim().toLowerCase()
  return isServiceModelType(normalized) ? normalized : "llm"
}

export function getProviderCapabilitiesForModelType(modelType: ServiceModelType): string[] {
  if (modelType === "llm") return ["llm", "chat", "json"]
  if (modelType === "vlm") return ["vlm", "chat", "vision", "json"]
  return getCapabilitiesForModelType(modelType)
}

export function getModelCapabilities(model: Pick<ProviderModelRecord, "model_type" | "capabilities">): string[] {
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

export function providerMatchesFilters(
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

export function providerTypeLabel(provider: ProviderConfig): string {
  const type = String(provider.provider_type || "")
  if (type === "deepseek") return "DeepSeek"
  if (type === "siliconflow") return "SiliconFlow"
  if (type === "anthropic") return "Anthropic"
  if (type === "codex_oauth") return "Codex OAuth"
  if (type === "agy_oauth") return "Antigravity OAuth"
  return "OpenAI-compatible"
}

export function isOAuthProvider(provider: ProviderConfig): boolean {
  return provider.provider_type === "codex_oauth" || provider.provider_type === "agy_oauth"
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

export function getProviderDefaultParams(providerId: string, modelType: ServiceModelType): Record<string, unknown> {
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

export function parseJsonObject(value: string): Record<string, unknown> {
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

export function getCustomProfiles(settings: Settings): CustomProfile[] {
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
