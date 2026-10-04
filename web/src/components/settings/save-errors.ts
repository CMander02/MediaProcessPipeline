import { createContext } from "react"

/** Why each setting key last failed to save, so the field can say it right next to itself. */
export const SettingsSaveErrors = createContext<Record<string, string>>({})
