import { useEffect } from "react"

import { getPreferences } from "@/hooks/use-preferences"
import { navigate } from "@/lib/router"

export function useAppStartup() {
  useEffect(() => {
    if (window.location.hash && window.location.hash !== "#/" && window.location.hash !== "#") return

    const preferences = getPreferences()
    if (preferences.startupPage === "last" && preferences.lastArchivePath) {
      navigate(`#/result/archive?path=${encodeURIComponent(preferences.lastArchivePath)}`, { replace: true })
      return
    }
    navigate("#/files", { replace: true })
  }, [])
}
