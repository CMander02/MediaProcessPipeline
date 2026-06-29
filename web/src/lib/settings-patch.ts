import {
  DEEPSEEK_STAGES,
  isSecretSettingKey,
  type CustomLLMProfile,
  type RuntimeSettings,
} from "./settings-schema"

export type { CustomLLMProfile, RuntimeSettings } from "./settings-schema"

export type SettingsPatchInput = Record<string, unknown>
export type SettingsPatch = Record<string, unknown>

const MASKED_SECRET_RE = /^(?:\*{3,}|\*{3,}\.{3}.{0,4})$/

const DOT_PATH_ALIASES: Record<string, string> = {
  "llm.provider": "llm_provider",
  "asr.provider": "asr_provider",
  "custom.profiles": "custom_llm_profiles",
  "custom.activeProfileId": "custom_active_profile_id",
  "custom.name": "custom_name",
  "custom.apiBase": "custom_api_base",
  "custom.model": "custom_model",
  "custom.apiKey": "custom_api_key",
  "deepseek.apiBase": "deepseek_api_base",
  "deepseek.apiKey": "deepseek_api_key",
  "local.modelPath": "local_llm_model_path",
  "local.device": "local_llm_device",
  "polish.provider": "polish_provider",
}

const DEEPSEEK_FIELD_NAMES = new Set(["model", "thinking", "effort"])
const DEEPSEEK_STAGE_NAMES = new Set<string>(DEEPSEEK_STAGES)

export function isMaskedSecret(value: unknown): value is string {
  return typeof value === "string" && MASKED_SECRET_RE.test(value)
}

function normalizeSettingsKey(key: string): string {
  const alias = DOT_PATH_ALIASES[key]
  if (alias) return alias

  const [section, stage, field] = key.split(".")
  if (
    section === "deepseek" &&
    DEEPSEEK_STAGE_NAMES.has(stage ?? "") &&
    DEEPSEEK_FIELD_NAMES.has(field ?? "")
  ) {
    return `deepseek_${stage}_${field}`
  }

  return key
}

export function expandSettingsPatch(input: SettingsPatchInput): SettingsPatch {
  return Object.entries(input).reduce<SettingsPatch>((patch, [key, value]) => {
    patch[normalizeSettingsKey(key)] = value
    return patch
  }, {})
}

function normalizeCustomProfile(profile: CustomLLMProfile): CustomLLMProfile {
  return {
    id: String(profile.id || "default"),
    name: String(profile.name || "Custom"),
    api_base: String(profile.api_base ?? ""),
    model: String(profile.model ?? ""),
    api_key: String(profile.api_key ?? ""),
  }
}

export function createSettingsPatch(input: SettingsPatchInput): SettingsPatch {
  const expanded = expandSettingsPatch(input)
  return Object.entries(expanded).reduce<SettingsPatch>((patch, [key, value]) => {
    if (isSecretSettingKey(key) && isMaskedSecret(value)) {
      return patch
    }
    patch[key] = value
    return patch
  }, {})
}

export function createCustomProfileMirrorPatch(
  current: RuntimeSettings,
  profiles: CustomLLMProfile[],
  activeProfileId = current.custom_active_profile_id,
): SettingsPatch {
  const normalizedProfiles = profiles.map(normalizeCustomProfile)
  const active =
    normalizedProfiles.find((profile) => profile.id === activeProfileId) ??
    normalizedProfiles[0] ??
    normalizeCustomProfile({
      id: "default",
      name: "Custom",
      api_base: "",
      model: "",
      api_key: "",
    })

  return createSettingsPatch({
    custom_llm_profiles: normalizedProfiles,
    custom_active_profile_id: active.id,
    custom_name: active.name,
    custom_api_base: active.api_base,
    custom_model: active.model,
    custom_api_key: active.api_key,
  })
}
