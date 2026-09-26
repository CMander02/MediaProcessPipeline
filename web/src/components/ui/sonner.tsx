import { useEffect, useState, type CSSProperties } from "react"
import { Toaster as Sonner, type ToasterProps } from "sonner"

export function Toaster(props: ToasterProps) {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"))

  useEffect(() => {
    const handleThemeChange = (event: Event) => {
      setDark(Boolean((event as CustomEvent<{ dark?: boolean }>).detail?.dark))
    }
    window.addEventListener("mpp:theme-change", handleThemeChange)
    return () => window.removeEventListener("mpp:theme-change", handleThemeChange)
  }, [])

  return (
    <Sonner
      theme={dark ? "dark" : "light"}
      position="bottom-right"
      closeButton
      richColors
      mobileOffset={{ bottom: "calc(4.5rem + var(--mpp-safe-bottom, 0px))" }}
      style={{
        "--normal-bg": "var(--popover)",
        "--normal-text": "var(--popover-foreground)",
        "--normal-border": "var(--border)",
      } as CSSProperties}
      {...props}
    />
  )
}
