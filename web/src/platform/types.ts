import type { Capabilities } from "@/lib/api"

export type PlatformKind = "web" | "android"

export interface ServerConnection {
  serverUrl: string
  configured: boolean
}

export interface ConnectInput {
  serverUrl: string
  token: string
}

export interface ConnectionCheck {
  serverUrl: string
  serverVersion?: string
  capabilities: Capabilities
}

export interface OfflineSyncStatus {
  syncing: boolean
  cursor: number
  archiveCount: number
  completedFiles: number
  totalFiles: number
  completedBytes: number
  totalBytes: number
  lastSync: number
  lastError: string
  message?: string
}

export interface OfflineArchiveFile {
  relative_path: string
  sha256: string
  size: number
  mime: string
  uri: string
}

export interface OfflineArchiveRecord extends Record<string, unknown> {
  archive_id: string
  path: string
  server_path?: string
  offline_files?: OfflineArchiveFile[]
}

export interface PlatformAdapter {
  readonly kind: PlatformKind
  readonly isNative: boolean
  initialize(): Promise<void>
  getConnection(): Promise<ServerConnection>
  connect(input: ConnectInput): Promise<ConnectionCheck>
  clearToken(): Promise<void>
  clearConnection(): Promise<void>
  applyCapabilities(capabilities: Capabilities): Capabilities
  getNetworkStatus(): Promise<boolean>
  openExternal(url: string): Promise<void>
  download(url: string): Promise<void>
  saveTextFile(filename: string, content: string): Promise<void>
  consumeSharedText(): string | null
  syncTheme(dark: boolean): Promise<void>
  getOfflineSyncStatus(): Promise<OfflineSyncStatus>
  listOfflineArchives(): Promise<OfflineArchiveRecord[]>
  getOfflineArchive(archiveId: string): Promise<OfflineArchiveRecord | null>
  readOfflineText(archiveId: string, relativePath: string): Promise<string>
  syncOfflineArchives(): Promise<OfflineSyncStatus>
  clearOfflineArchives(): Promise<OfflineSyncStatus>
  rebuildOfflineIndex(): Promise<OfflineSyncStatus>
}
