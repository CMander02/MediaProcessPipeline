import { useContext } from "react"

import { PlatformContext } from "@/platform/platform-context-state"
import type { PlatformAdapter } from "@/platform/types"

export function usePlatform(): PlatformAdapter {
  return useContext(PlatformContext)
}
