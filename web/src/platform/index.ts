import type { PlatformAdapter } from "@/platform/types"

let platformPromise: Promise<PlatformAdapter> | null = null

export function initializePlatform(): Promise<PlatformAdapter> {
  platformPromise ??= createPlatform()
  return platformPromise
}

async function createPlatform(): Promise<PlatformAdapter> {
  const capacitor = (window as Window & {
    Capacitor?: { isNativePlatform?: () => boolean; getPlatform?: () => string }
  }).Capacitor
  const isAndroid = Boolean(capacitor?.isNativePlatform?.() && capacitor.getPlatform?.() === "android")
  const adapter = isAndroid
    ? (await import("@/platform/capacitor-platform")).createCapacitorPlatform()
    : (await import("@/platform/web-platform")).createWebPlatform()
  await adapter.initialize()
  return adapter
}

export type { ConnectInput, ConnectionCheck, OfflineArchiveFile, OfflineArchiveRecord, OfflineSyncStatus, PlatformAdapter, PlatformKind, ServerConnection } from "@/platform/types"
