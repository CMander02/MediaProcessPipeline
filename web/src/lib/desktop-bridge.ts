/**
 * The desktop app (Electron) exposes a small bridge to the MPP page: the actions of the old
 * native menu and the colours of the window buttons drawn over the title bar.
 * In a browser or the Android app there is no bridge and these features stay hidden.
 */

export type DesktopAction = "open-browser" | "reconnect" | "choose-project" | "open-logs" | "quit"

export interface DesktopAppInfo {
  /** The desktop app started the backend and stops it on quit */
  owned: boolean
  version: string
}

export interface DesktopAppBridge {
  action(name: DesktopAction): Promise<unknown>
  info(): Promise<DesktopAppInfo>
  setTitleBarColors(colors: { color: string; symbolColor: string }): Promise<unknown>
}

export const desktopApp: DesktopAppBridge | null = typeof window === "undefined"
  ? null
  : (window as unknown as { mppDesktopApp?: DesktopAppBridge }).mppDesktopApp ?? null

export const isDesktopApp = desktopApp !== null

/** Any CSS colour as #rrggbb, which is what Electron accepts for the window button overlay. */
export function cssColorToHex(color: string): string | null {
  const canvas = document.createElement("canvas")
  canvas.width = 1
  canvas.height = 1
  const context = canvas.getContext("2d")
  if (!context) return null
  context.fillStyle = "#000000"
  context.fillStyle = color
  context.fillRect(0, 0, 1, 1)
  const [r, g, b] = context.getImageData(0, 0, 1, 1).data
  return `#${[r, g, b].map((part) => part.toString(16).padStart(2, "0")).join("")}`
}
