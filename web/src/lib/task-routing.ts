export const PREFERRED_WORKER_OPTION = "_mpp_preferred_worker_id"

export type ProcessingTarget = "server" | "exe" | `worker:${string}`

const HTTP_URL_PATTERN = /^https?:\/\//i
const BILIBILI_VIDEO_ID_PATTERN = /^BV[0-9A-Za-z]{10}(?::p\d+)?$/i
const LOCAL_PATH_PREFIX_PATTERN = /^(?:file:\/\/|[A-Za-z]:[\\/]|\\\\|\/\/|\/|\.{1,2}[\\/]|~[\\/])/i
const LOCAL_MEDIA_FILENAME_PATTERN =
  /\.(?:mp4|mkv|avi|webm|mov|flv|wmv|mp3|wav|aac|flac|ogg|m4a|wma)$/i

export function isLocalPathSource(source: string): boolean {
  const value = source.trim()
  if (!value || HTTP_URL_PATTERN.test(value) || BILIBILI_VIDEO_ID_PATTERN.test(value)) {
    return false
  }
  return (
    LOCAL_PATH_PREFIX_PATTERN.test(value)
    || value.includes("\\")
    || value.includes("/")
    || LOCAL_MEDIA_FILENAME_PATTERN.test(value)
  )
}

export function isServerProcessingTarget(target: ProcessingTarget): boolean {
  return target === "server"
}

export function preferredWorkerId(target: ProcessingTarget): string | undefined {
  if (!target.startsWith("worker:")) return undefined
  const workerId = target.slice("worker:".length).trim()
  return workerId || undefined
}

export function requestedExecutorForTarget(
  target: ProcessingTarget,
): "server" | "exe" {
  return isServerProcessingTarget(target) ? "server" : "exe"
}

export function withProcessingTargetOptions(
  options: Record<string, unknown>,
  target: ProcessingTarget,
): Record<string, unknown> {
  const nextOptions = { ...options }
  const workerId = preferredWorkerId(target)
  if (workerId) {
    nextOptions[PREFERRED_WORKER_OPTION] = workerId
  } else {
    delete nextOptions[PREFERRED_WORKER_OPTION]
  }
  return nextOptions
}

export function isExeLocalSourceBlocked({
  remoteSyncEnabled,
  target,
  source,
  hasStagedFiles = false,
}: {
  remoteSyncEnabled: boolean
  target: ProcessingTarget
  source?: string
  hasStagedFiles?: boolean
}): boolean {
  return (
    remoteSyncEnabled
    && isServerProcessingTarget(target)
    && (hasStagedFiles || isLocalPathSource(source ?? ""))
  )
}
