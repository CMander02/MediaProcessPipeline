import { useContext, type ReactNode } from "react"
import { createPortal } from "react-dom"

import { HeaderSlotContext } from "@/components/app-shell/header-slot-context"

/** Render children into the title bar instead of the page. The main navigation hides meanwhile. */
export function HeaderContext({ children }: { children: ReactNode }) {
  const slot = useContext(HeaderSlotContext)
  return slot ? createPortal(children, slot) : null
}
