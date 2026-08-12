export function normalizeServerUrl(value: string): string {
  let parsed: URL
  try {
    parsed = new URL(value.trim())
  } catch {
    throw new Error("请输入完整的服务器地址，例如 https://mpp.example.com")
  }
  if (parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new Error("服务器地址只填写协议、域名和端口")
  }
  if (parsed.pathname !== "/") {
    throw new Error("服务器地址无需包含 /api 或其他路径")
  }
  if (parsed.protocol !== "https:" && parsed.protocol !== "http:") {
    throw new Error("服务器地址需使用 HTTPS 或 HTTP")
  }
  if (parsed.protocol === "http:" && !isDevelopmentHttpHost(parsed.hostname)) {
    throw new Error("公网服务器请使用 HTTPS；HTTP 仅用于 localhost 或局域网调试")
  }
  return parsed.origin
}

export function bundledDefaultServerUrl(
  value = import.meta.env.VITE_MPP_DEFAULT_SERVER_URL,
): string {
  const candidate = value?.trim()
  return candidate ? normalizeServerUrl(candidate) : ""
}

function isDevelopmentHttpHost(hostname: string) {
  const host = hostname.toLowerCase().replace(/^\[|\]$/g, "")
  if (host === "localhost" || host === "::1" || host.startsWith("127.")) return true
  if (/^10(?:\.\d{1,3}){3}$/.test(host)) return true
  if (/^192\.168(?:\.\d{1,3}){2}$/.test(host)) return true
  const private172 = /^172\.(\d{1,3})(?:\.\d{1,3}){2}$/.exec(host)
  return private172 ? Number(private172[1]) >= 16 && Number(private172[1]) <= 31 : false
}
