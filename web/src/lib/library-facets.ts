/**
 * Latest facet counts from the library page, shared with the header filter menus
 * (which render outside the page) so dropdown options can show how many items each has.
 */
import { useSyncExternalStore } from "react"

import type { ArchiveFacets } from "@/repositories/archive-types"

let facets: ArchiveFacets | null = null
const listeners = new Set<() => void>()

export function publishLibraryFacets(next: ArchiveFacets | null | undefined) {
  if ((next ?? null) === facets) return
  facets = next ?? null
  listeners.forEach((listener) => listener())
}

export function useLibraryFacets(): ArchiveFacets | null {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    () => facets,
  )
}
