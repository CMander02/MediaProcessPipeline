/**
 * Model release date registry.
 * NEW! badge is shown for models released within the last 7 days.
 * Add new entries here when new models ship.
 */
export const MODEL_RELEASE_DATES: Record<string, string> = {
  // Claude 4.x family
  "claude-opus-4-7":               "2026-04-25",
  "claude-sonnet-4-6":             "2026-04-25",
  "claude-haiku-4-5":              "2026-04-25",
  "claude-haiku-4-5-20251001":     "2026-04-25",

  // DeepSeek v4
  "deepseek-v4-flash":             "2026-04-22",
  "deepseek-v4-pro":               "2026-04-22",

  // Qwen3-ASR
  "Qwen3-ASR":                     "2026-04-28",
  "Qwen/Qwen3-ASR-1.7B":          "2026-04-28",
  "Qwen3-ASR-1.7B":               "2026-04-28",
}

const NEW_WINDOW_DAYS = 7

export function isNewModel(modelName: string): boolean {
  const releaseStr = MODEL_RELEASE_DATES[modelName]
  if (!releaseStr) return false
  const releaseDate = new Date(releaseStr)
  const now = new Date()
  const diffMs = now.getTime() - releaseDate.getTime()
  const diffDays = diffMs / (1000 * 60 * 60 * 24)
  return diffDays >= 0 && diffDays <= NEW_WINDOW_DAYS
}
