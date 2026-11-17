export interface UpdateAsset {
  name: string
  url: string
  size: number
  recommended: boolean
}

export interface UpdateCheckResponse {
  current_version: string
  latest_version: string
  update_available: boolean
  release_url: string
  published_at: string | null
  notes: string
  assets: UpdateAsset[]
}

export interface UpdateDownloadResponse {
  path: string
  sha256: string
  asset_name: string
  version: string
  instructions: string
}

function isWebUrl(value: unknown): value is string {
  if (typeof value !== "string") return false
  try {
    const url = new URL(value)
    return url.protocol === "https:" || url.protocol === "http:"
  } catch {
    return false
  }
}

function isUpdateAsset(value: unknown): value is UpdateAsset {
  return typeof value === "object" && value !== null
    && "name" in value && typeof value.name === "string"
    && "url" in value && isWebUrl(value.url)
    && "size" in value && typeof value.size === "number" && Number.isFinite(value.size) && value.size >= 0
    && "recommended" in value && typeof value.recommended === "boolean"
}

export function parseUpdateCheck(value: unknown): UpdateCheckResponse {
  if (typeof value !== "object" || value === null
    || !("current_version" in value) || typeof value.current_version !== "string"
    || !("latest_version" in value) || typeof value.latest_version !== "string"
    || !("update_available" in value) || typeof value.update_available !== "boolean"
    || !("release_url" in value) || !isWebUrl(value.release_url)
    || !("published_at" in value) || (typeof value.published_at !== "string" && value.published_at !== null)
    || !("notes" in value) || typeof value.notes !== "string"
    || !("assets" in value) || !Array.isArray(value.assets) || !value.assets.every(isUpdateAsset)) {
    throw new Error("更新检查响应格式无效")
  }
  return {
    current_version: value.current_version, latest_version: value.latest_version,
    update_available: value.update_available, release_url: value.release_url,
    published_at: value.published_at, notes: value.notes, assets: value.assets,
  }
}

export function parseUpdateDownload(value: unknown): UpdateDownloadResponse {
  if (typeof value !== "object" || value === null
    || !("path" in value) || typeof value.path !== "string"
    || !("sha256" in value) || typeof value.sha256 !== "string" || !/^[a-f\d]{64}$/i.test(value.sha256)
    || !("asset_name" in value) || typeof value.asset_name !== "string"
    || !("version" in value) || typeof value.version !== "string"
    || !("instructions" in value) || typeof value.instructions !== "string") {
    throw new Error("更新下载响应格式无效")
  }
  return {
    path: value.path, sha256: value.sha256, asset_name: value.asset_name,
    version: value.version, instructions: value.instructions,
  }
}
