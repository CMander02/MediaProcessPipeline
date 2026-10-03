export type ProxyMode = "system" | "none" | "custom"

/** Values the backend reads as "connect directly" (see app/core/network.py). */
const DISABLED_PROXY_VALUES = new Set(["direct", "none", "off", "false", "0"])

export function proxyMode(value: string): ProxyMode {
  const normalized = value.trim().toLowerCase()
  if (!normalized) return "system"
  if (DISABLED_PROXY_VALUES.has(normalized)) return "none"
  return "custom"
}

/** What a proxy value means, in the words the proxy select uses; credentials in the URL are left out. */
export function describeProxy(value: string): string {
  const mode = proxyMode(value)
  if (mode === "system") return "系统代理"
  if (mode === "none") return "无代理"
  return value.trim().replace(/\/\/[^/@\s]+@/, "//")
}
