import { ref, watch, onMounted } from "vue"

export type ThemeMode = "light" | "dark" | "system"

const STORAGE_KEY = "pipeline-theme"

// 全局状态
const themeMode = ref<ThemeMode>("system")
const isDark = ref(false)

// 监听系统主题变化
const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)")

function updateDarkClass() {
  if (themeMode.value === "system") {
    isDark.value = mediaQuery.matches
  } else {
    isDark.value = themeMode.value === "dark"
  }

  if (isDark.value) {
    document.documentElement.classList.add("dark")
  } else {
    document.documentElement.classList.remove("dark")
  }
}

// 初始化
function initTheme() {
  const stored = localStorage.getItem(STORAGE_KEY) as ThemeMode | null
  if (stored && ["light", "dark", "system"].includes(stored)) {
    themeMode.value = stored
  }
  updateDarkClass()

  // 监听系统主题变化
  mediaQuery.addEventListener("change", updateDarkClass)
}

export function useTheme() {
  onMounted(() => {
    initTheme()
  })

  watch(themeMode, (newMode) => {
    localStorage.setItem(STORAGE_KEY, newMode)
    updateDarkClass()
  })

  const setTheme = (mode: ThemeMode) => {
    themeMode.value = mode
  }

  const toggleTheme = () => {
    if (themeMode.value === "light") {
      themeMode.value = "dark"
    } else if (themeMode.value === "dark") {
      themeMode.value = "system"
    } else {
      themeMode.value = "light"
    }
  }

  return {
    themeMode,
    isDark,
    setTheme,
    toggleTheme,
  }
}
