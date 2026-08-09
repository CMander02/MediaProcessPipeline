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
}
