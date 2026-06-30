export const DEEPSEEK_STAGES = ["analyze", "polish", "summary", "mindmap"] as const

export type DeepSeekStage = (typeof DEEPSEEK_STAGES)[number]
export type LlmProvider = "local" | "deepseek" | "custom" | "anthropic" | "openai"
export type AsrProvider = "qwen3" | "siliconflow"
export type DeviceValue = "cuda" | "cpu" | "auto"
export type ModelCapability = "llm" | "vlm" | "chat" | "fast" | "thinking" | "reasoning" | "vision" | "asr" | "embedding" | "rerank" | "local" | "json"
export type ServiceModelType = "llm" | "vlm" | "embedding" | "rerank" | "asr"
export type ModelStage = DeepSeekStage | "asr" | "vision" | "embedding"

export interface CustomLLMProfile {
  id: string
  name: string
  api_base: string
  model: string
  api_key: string
  [key: string]: unknown
}

export interface ServiceModelRecord {
  id: string
  connection_id: string
  model_id: string
  display_name?: string
  model_type?: ServiceModelType | string
  capabilities?: ModelCapability[] | string[]
  endpoint_path?: string
  enabled?: boolean
  default_params?: Record<string, unknown>
  [key: string]: unknown
}

export interface ProviderModelRecord {
  id: string
  model_id: string
  display_name?: string
  enabled?: boolean
  model_type?: ServiceModelType | string
  capabilities?: ModelCapability[] | string[]
  endpoint_path?: string
  default_params?: Record<string, unknown>
  [key: string]: unknown
}

export interface ProviderBalanceConfig {
  enabled?: boolean
  endpoint_path?: string
  method?: string
  [key: string]: unknown
}

export interface ProviderConfig {
  id: string
  name: string
  provider_type?: string
  enabled?: boolean
  api_base?: string
  api_key?: string
  api_mode?: string
  headers?: Record<string, unknown>
  extra_body?: Record<string, unknown>
  balance?: ProviderBalanceConfig
  models?: ProviderModelRecord[]
  [key: string]: unknown
}

export interface RuntimeModelBinding {
  provider_id: string
  model_id: string
  capability: string
  [key: string]: unknown
}

export interface RuntimeSettings {
  llm_provider?: LlmProvider | string
  asr_provider?: AsrProvider | string

  anthropic_api_key?: string
  anthropic_api_base?: string
  anthropic_model?: string
  openai_api_key?: string
  openai_api_base?: string
  openai_model?: string
  custom_api_key?: string
  custom_api_base?: string
  custom_model?: string
  custom_name?: string
  custom_llm_profiles?: CustomLLMProfile[]
  custom_active_profile_id?: string
  service_models?: ServiceModelRecord[]
  service_connections?: Record<string, unknown>[]
  providers?: ProviderConfig[]
  deleted_provider_ids?: string[]
  runtime_model_bindings?: Record<string, RuntimeModelBinding>

  deepseek_api_key?: string
  deepseek_api_base?: string
  deepseek_analyze_model?: string
  deepseek_analyze_thinking?: string
  deepseek_analyze_effort?: string
  deepseek_polish_model?: string
  deepseek_polish_thinking?: string
  deepseek_polish_effort?: string
  deepseek_summary_model?: string
  deepseek_summary_thinking?: string
  deepseek_summary_effort?: string
  deepseek_mindmap_model?: string
  deepseek_mindmap_thinking?: string
  deepseek_mindmap_effort?: string

  qwen3_asr_model_path?: string
  qwen3_aligner_model_path?: string
  qwen3_device?: DeviceValue | string
  siliconflow_api_base?: string
  siliconflow_api_key?: string
  siliconflow_asr_model?: string
  jina_reader_enabled?: boolean
  jina_reader_api_base?: string
  jina_reader_api_key?: string
  jina_reader_bypass_cache?: boolean
  web_scrape_timeout_sec?: number

  local_llm_model_path?: string
  local_llm_device?: DeviceValue | string
  local_llm_n_gpu_layers?: number
  local_llm_n_ctx?: number
  local_llm_n_batch?: number
  polish_provider?: LlmProvider | "" | string

  api_token?: string
  data_root?: string

  [key: string]: unknown
}

export const SECRET_SETTING_KEYS = [
  "api_token",
  "anthropic_api_key",
  "openai_api_key",
  "custom_api_key",
  "deepseek_api_key",
  "hf_token",
  "hf_proxy",
  "siliconflow_api_key",
  "bilibili_sessdata",
  "bilibili_bili_jct",
  "bilibili_dede_user_id",
  "xiaohongshu_cookie",
  "vlm_api_key",
  "kb_embedding_api_key",
  "jina_reader_api_key",
] as const

export type SecretSettingKey = (typeof SECRET_SETTING_KEYS)[number]

const SECRET_SETTING_KEY_SET = new Set<string>(SECRET_SETTING_KEYS)

export function isSecretSettingKey(key: string): key is SecretSettingKey {
  return SECRET_SETTING_KEY_SET.has(key)
}
