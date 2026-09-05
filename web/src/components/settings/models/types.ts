import { type Settings } from "@/lib/api"
import { type ReactNode } from "react"

type UpdateSetting = (key: string, value: unknown) => Promise<void>

export type UpdateSettings = (updates: Record<string, unknown>) => Promise<void>

export interface SharedSettingsProps {
  settings: Settings
  updateSetting: UpdateSetting
  saving: Record<string, boolean>
  saved: Record<string, boolean>
}

export interface PurposeModelBindingsProps {
  settings: Settings
  updateSetting: UpdateSetting
}

export interface ModelBindingOption {
  value: string
  label: string
}

export interface PurposeBindingDef {
  key: string
  label: string
  description: string
  options: ModelBindingOption[]
  fallback: string
}

export interface RegistrySettingsProps extends SharedSettingsProps {
  visibleLlmProvider: string
  updateSettings: UpdateSettings
}

export interface LocalModelSettingsProps extends SharedSettingsProps {
  detectLocalUvr: () => Promise<void>
  uvrDetecting: boolean
  uvrDetection: string | null
}

export interface ModelListItem {
  id: string
  title: string
  description: string
  badge: string
  icon?: ReactNode
  status?: string
  searchText?: string
}

export interface CustomProfile {
  id: string
  name: string
  api_base: string
  model: string
  api_key: string
}
