import { useEffect, useState } from "react"

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
    return window.matchMedia("(max-width: 767px)").matches
  })

  useEffect(() => {
    const query = window.matchMedia("(max-width: 767px)")
    const update = () => setMobile(query.matches)
    update()
    query.addEventListener("change", update)
    return () => query.removeEventListener("change", update)
  }, [])

  return mobile
}
