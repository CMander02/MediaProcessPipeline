export type ThemePreference = "system" | "light" | "dark"

const THEME_KEY = "theme"
const SYSTEM_DARK_QUERY = "(prefers-color-scheme: dark)"

function themeStorage(): Storage | null {
  try {
    return window.localStorage ?? null
  } catch {
    return null
  }
}

export function getThemePreference(): ThemePreference {
  const stored = themeStorage()?.getItem(THEME_KEY)
  return stored === "light" || stored === "dark" ? stored : "system"
}

export function applyThemePreference(preference = getThemePreference()) {
  const dark = preference === "dark"
    || (preference === "system" && window.matchMedia(SYSTEM_DARK_QUERY).matches)
  document.documentElement.classList.toggle("dark", dark)
  return dark
}

export function setThemePreference(preference: ThemePreference) {
  themeStorage()?.setItem(THEME_KEY, preference)
  const dark = applyThemePreference(preference)
  window.dispatchEvent(new CustomEvent("mpp:theme-change", { detail: { preference, dark } }))
}

export function watchSystemTheme() {
  const query = window.matchMedia(SYSTEM_DARK_QUERY)
  const handleChange = () => {
    if (getThemePreference() !== "system") return
    const dark = applyThemePreference("system")
    window.dispatchEvent(new CustomEvent("mpp:theme-change", { detail: { preference: "system", dark } }))
  }
  query.addEventListener("change", handleChange)
  return () => query.removeEventListener("change", handleChange)
}
