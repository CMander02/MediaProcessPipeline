import type {
  LlmProvider,
  ModelCapability,
  ModelStage,
  ServiceModelRecord,
  ServiceModelType,
} from "./settings-schema"

export type ModelProvider = LlmProvider | "siliconflow" | "qwen3" | "vlm" | "kb_embedding"

export interface ModelRegistryEntry {
  value: string
  label: string
  provider: ModelProvider
  capabilities: ModelCapability[]
  stages?: ModelStage[]
}

export interface ModelOptionFilter {
  provider?: ModelProvider
  capability?: ModelCapability
  stage?: ModelStage
  query?: string
}

export type KnownModelOption = ModelRegistryEntry & { kind: "known" }

export interface FreeTextModelOption {
  kind: "free-text"
  value: string
  label: string
  provider: ModelProvider
}

export type ModelOption = KnownModelOption | FreeTextModelOption

export const SERVICE_MODEL_TYPES: Array<{
  value: ServiceModelType
  label: string
  shortLabel: string
  description: string
}> = [
  { value: "llm", label: "LLM", shortLabel: "LLM", description: "文本对话、总结、分析和结构化输出" },
  { value: "vlm", label: "VLM", shortLabel: "视觉", description: "图文理解、多模态对话和图片问答" },
  { value: "embedding", label: "Embedding", shortLabel: "嵌入", description: "知识库向量化和语义检索" },
  { value: "rerank", label: "Rerank", shortLabel: "重排", description: "检索结果重排序" },
  { value: "asr", label: "ASR", shortLabel: "ASR", description: "语音识别转写" },
]

const SERVICE_MODEL_CAPABILITIES: Record<ServiceModelType, ModelCapability[]> = {
  llm: ["chat"],
  vlm: ["chat", "vision"],
  embedding: ["embedding"],
  rerank: ["rerank"],
  asr: ["asr"],
}

const SERVICE_MODEL_ENDPOINT_PATHS: Record<ServiceModelType, string> = {
  llm: "/chat/completions",
  vlm: "/chat/completions",
  embedding: "/embeddings",
  rerank: "/rerank",
  asr: "/audio/transcriptions",
}

export const MODEL_REGISTRY: ModelRegistryEntry[] = [
  {
    value: "deepseek-v4-flash",
    label: "DeepSeek V4 Flash",
    provider: "deepseek",
    capabilities: ["chat", "fast"],
    stages: ["analyze", "polish", "mindmap"],
  },
  {
    value: "deepseek-v4-pro",
    label: "DeepSeek V4 Pro",
    provider: "deepseek",
    capabilities: ["chat", "thinking"],
    stages: ["summary"],
  },
  {
    value: "claude-sonnet-4-20250514",
    label: "Claude Sonnet 4",
    provider: "anthropic",
    capabilities: ["chat", "reasoning"],
    stages: ["analyze", "polish", "summary", "mindmap"],
  },
  {
    value: "gpt-4o",
    label: "GPT-4o",
    provider: "openai",
    capabilities: ["chat", "vision"],
    stages: ["analyze", "summary", "mindmap", "vision"],
  },
  {
    value: "gpt-4o-mini",
    label: "GPT-4o mini",
    provider: "openai",
    capabilities: ["chat", "fast"],
    stages: ["analyze", "polish", "summary", "mindmap"],
  },
  {
    value: "FunAudioLLM/SenseVoiceSmall",
    label: "SenseVoice Small",
    provider: "siliconflow",
    capabilities: ["asr", "fast"],
    stages: ["asr"],
  },
  {
    value: "Qwen/Qwen3-ASR",
    label: "Qwen3-ASR",
    provider: "qwen3",
    capabilities: ["asr", "local"],
    stages: ["asr"],
  },
  {
    value: "qwen3-embedding-0.6b",
    label: "Qwen3 Embedding 0.6B",
    provider: "kb_embedding",
    capabilities: ["embedding"],
    stages: ["embedding"],
  },
  {
    value: "BAAI/bge-reranker-v2-m3",
    label: "BGE Reranker v2 M3",
    provider: "siliconflow",
    capabilities: ["rerank"],
  },
]

const FREE_TEXT_PROVIDERS = new Set<ModelProvider>([
  "anthropic",
  "openai",
  "deepseek",
  "custom",
  "local",
  "siliconflow",
  "qwen3",
  "vlm",
  "kb_embedding",
])

function matchesQuery(entry: ModelRegistryEntry, query: string): boolean {
  const normalized = query.trim().toLowerCase()
  if (!normalized) return true
  return (
    entry.value.toLowerCase().includes(normalized) ||
    entry.label.toLowerCase().includes(normalized)
  )
}

export function isFreeTextModelAllowed(provider: ModelProvider): boolean {
  return FREE_TEXT_PROVIDERS.has(provider)
}

export function isServiceModelType(value: unknown): value is ServiceModelType {
  return SERVICE_MODEL_TYPES.some((type) => type.value === value)
}

export function getCapabilitiesForModelType(modelType: ServiceModelType): ModelCapability[] {
  return [...SERVICE_MODEL_CAPABILITIES[modelType]]
}

export function getEndpointPathForModelType(modelType: ServiceModelType): string {
  return SERVICE_MODEL_ENDPOINT_PATHS[modelType]
}

export function getModelTypeFromCapabilities(
  capabilities: Array<ModelCapability | string> = [],
  fallback: ServiceModelType = "llm",
): ServiceModelType {
  const normalized = new Set(capabilities.map((capability) => String(capability).toLowerCase()))
  if (normalized.has("asr")) return "asr"
  if (normalized.has("rerank")) return "rerank"
  if (normalized.has("embedding")) return "embedding"
  if (normalized.has("vision")) return "vlm"
  if (normalized.has("chat")) return "llm"
  return fallback
}

export function normalizeServiceModelType(model: Pick<ServiceModelRecord, "model_type" | "capabilities">): ServiceModelType {
  const modelType = String(model.model_type ?? "").toLowerCase()
  if (isServiceModelType(modelType)) return modelType
  return getModelTypeFromCapabilities(model.capabilities)
}

function serviceModelSlug(connectionId: string, modelId: string): string {
  return `${connectionId}:${modelId.trim().toLowerCase().replace(/[/:]/g, "-")}`
}

export function createServiceModelRecord({
  connectionId,
  modelId,
  modelType,
}: {
  connectionId: string
  modelId: string
  modelType: ServiceModelType
}): ServiceModelRecord {
  const trimmedModelId = modelId.trim()
  return {
    id: serviceModelSlug(connectionId, trimmedModelId),
    connection_id: connectionId,
    model_id: trimmedModelId,
    display_name: trimmedModelId,
    model_type: modelType,
    capabilities: getCapabilitiesForModelType(modelType),
    endpoint_path: getEndpointPathForModelType(modelType),
    enabled: true,
    default_params: {},
  }
}

export function getModelOptions(filter: ModelOptionFilter = {}): ModelOption[] {
  const query = filter.query?.trim() ?? ""
  const known = MODEL_REGISTRY.filter((entry) => {
    if (filter.provider && entry.provider !== filter.provider) return false
    if (filter.capability && !entry.capabilities.includes(filter.capability)) return false
    if (filter.stage && entry.stages && !entry.stages.includes(filter.stage)) return false
    return matchesQuery(entry, query)
  }).map<KnownModelOption>((entry) => ({ ...entry, kind: "known" }))

  if (!query || !filter.provider || !isFreeTextModelAllowed(filter.provider)) {
    return known
  }

  const hasExactMatch = known.some((entry) => entry.value.toLowerCase() === query.toLowerCase())
  if (hasExactMatch) return known

  return [
    ...known,
    {
      kind: "free-text",
      value: query,
      label: query,
      provider: filter.provider,
    },
  ]
}
