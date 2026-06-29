import type {
  LlmProvider,
  ModelCapability,
  ModelStage,
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
