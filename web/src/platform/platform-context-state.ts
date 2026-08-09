import { createContext } from "react"

import type { PlatformAdapter } from "@/platform/types"
import { createWebPlatform } from "@/platform/web-platform"

export const PlatformContext = createContext<PlatformAdapter>(createWebPlatform())
