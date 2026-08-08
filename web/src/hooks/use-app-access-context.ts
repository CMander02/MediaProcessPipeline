import { createContext, useContext } from "react"

import type { AuthStatus, Capabilities } from "@/lib/api"

export interface AppAccessValue {
  auth: AuthStatus
  capabilities: Capabilities
  refresh: () => Promise<void>
}

export const AppAccessContext = createContext<AppAccessValue | null>(null)

export function useAppAccess(): AppAccessValue {
  const value = useContext(AppAccessContext)
  if (!value) throw new Error("useAppAccess must be used inside AppAccessBoundary")
  return value
}
