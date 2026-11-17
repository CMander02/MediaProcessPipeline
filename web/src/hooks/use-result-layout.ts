import { useCallback, useEffect, useMemo, useState } from "react"
import type { Layout } from "react-resizable-panels"

const PANEL_RATIO_KEY = "mpp-result-panel-ratio"

function loadPanelRatio() {
  try {
    const ratio = Number(localStorage.getItem(PANEL_RATIO_KEY) ?? 50)
    if (Number.isFinite(ratio) && ratio >= 20 && ratio <= 70) return ratio
  } catch {
    return 50
  }
  return 50
}

export function useResultPanelLayout() {
  const [ratio, setRatio] = useState(loadPanelRatio)
  const defaultLayout = useMemo(() => ({ media: ratio, content: 100 - ratio }), [ratio])
  const onLayoutChanged = useCallback((layout: Layout) => {
    const ratio = layout.media
    setRatio(ratio)
    try { localStorage.setItem(PANEL_RATIO_KEY, String(ratio)) } catch { return }
  }, [])
  return { defaultLayout, onLayoutChanged }
}

export function usePortraitResultLayout(): boolean {
  const [portrait, setPortrait] = useState(() => {
    if (typeof window === "undefined") return false
    return window.matchMedia("(orientation: portrait)").matches
  })

  useEffect(() => {
    const query = window.matchMedia("(orientation: portrait)")
    const update = () => setPortrait(query.matches)
    update()
    query.addEventListener("change", update)
    window.addEventListener("resize", update)
    return () => {
      query.removeEventListener("change", update)
      window.removeEventListener("resize", update)
    }
  }, [])

  return portrait
}

export function useMobileResultLayout(): boolean {
  const [mobile, setMobile] = useState(() => {
    if (typeof window === "undefined") return false
    return window.matchMedia("(max-width: 767px), (pointer: coarse) and (max-height: 500px)").matches
  })

  useEffect(() => {
    const query = window.matchMedia("(max-width: 767px), (pointer: coarse) and (max-height: 500px)")
    const update = () => setMobile(query.matches)
    update()
    query.addEventListener("change", update)
    return () => query.removeEventListener("change", update)
  }, [])

  return mobile
}
