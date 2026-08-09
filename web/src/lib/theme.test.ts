/** @vitest-environment jsdom */

import { afterEach, describe, expect, it, vi } from "vitest"

import { applyThemePreference, getThemePreference, setThemePreference, watchSystemTheme } from "@/lib/theme"

afterEach(() => {
  document.documentElement.classList.remove("dark")
  vi.unstubAllGlobals()
})

function mockSystemTheme(initialDark: boolean) {
  const values = new Map<string, string>()
  const storage = {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
    clear: () => values.clear(),
    key: () => null,
    get length() { return values.size },
  } satisfies Storage
  Object.defineProperty(window, "localStorage", { configurable: true, value: storage })
  let dark = initialDark
  let listener: (() => void) | null = null
  vi.stubGlobal("matchMedia", vi.fn(() => ({
    get matches() { return dark },
    media: "(prefers-color-scheme: dark)",
    onchange: null,
    addEventListener: (_name: string, next: () => void) => { listener = next },
    removeEventListener: () => { listener = null },
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })))
  return {
    change(nextDark: boolean) {
      dark = nextDark
      listener?.()
    },
  }
}

describe("theme preference", () => {
  it("uses the system theme when no preference is saved", () => {
    mockSystemTheme(true)

    expect(getThemePreference()).toBe("system")
    expect(applyThemePreference()).toBe(true)
    expect(document.documentElement.classList.contains("dark")).toBe(true)
  })

  it("keeps an explicit theme independent from system changes", () => {
    const system = mockSystemTheme(false)
    setThemePreference("dark")
    const stop = watchSystemTheme()

    system.change(false)

    expect(getThemePreference()).toBe("dark")
    expect(document.documentElement.classList.contains("dark")).toBe(true)
    stop()
  })

  it("updates a system preference while the app is running", () => {
    const system = mockSystemTheme(false)
    setThemePreference("system")
    const stop = watchSystemTheme()

    system.change(true)

    expect(document.documentElement.classList.contains("dark")).toBe(true)
    stop()
  })
})
