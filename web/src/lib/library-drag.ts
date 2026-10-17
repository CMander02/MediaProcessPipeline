import type { DragEvent } from "react"
import { archiveCacheScope } from "@/lib/archive-cache-scope"

export const LIBRARY_DRAG_TYPE = "application/x-mpp-library-content"
export function startLibraryDrag(event: DragEvent, keys: string[]) {
  event.dataTransfer.effectAllowed = "move"
  event.dataTransfer.setData(LIBRARY_DRAG_TYPE, JSON.stringify({ scope: archiveCacheScope(), keys: [...new Set(keys)] }))
  event.dataTransfer.setData("text/plain", `${keys.length} 项内容`)
}
export function isLibraryDrag(event: DragEvent) {
  return Array.from(event.dataTransfer.types).includes(LIBRARY_DRAG_TYPE)
}
export function libraryDragKeys(event: DragEvent): string[] {
  try {
    const payload = JSON.parse(event.dataTransfer.getData(LIBRARY_DRAG_TYPE))
    if (payload.scope !== archiveCacheScope() || !Array.isArray(payload.keys)) return []
    return payload.keys.filter((key: unknown): key is string => typeof key === "string" && key.length > 0)
  } catch { return [] }
}
