import { useEffect, useState, type ReactNode } from "react"
import { useAppAccess } from "@/hooks/use-app-access-context"
import { isLibraryDrag, libraryDragKeys } from "@/lib/library-drag"
import { cn } from "@/lib/utils"

export function LibraryDropTarget({ children, onDropContents, className, label, disabled = false }: {
  children: ReactNode; onDropContents: (keys: string[]) => void; className?: string; label: string; disabled?: boolean
}) {
  const { online, capabilities } = useAppAccess()
  const [over, setOver] = useState(false)
  const enabled = online && capabilities.archive_mutation && !disabled
  useEffect(() => {
    const clear = () => setOver(false)
    window.addEventListener("dragend", clear)
    window.addEventListener("drop", clear)
    return () => { window.removeEventListener("dragend", clear); window.removeEventListener("drop", clear) }
  }, [])
  return <div className={cn("min-w-0 rounded-md", over && enabled && "bg-primary/10 ring-2 ring-primary", className)}
    data-drop-folder={label} data-drag-over={over && enabled ? "true" : undefined}
    onDragOver={(event) => {
      if (!enabled || !isLibraryDrag(event)) return
      event.preventDefault(); event.stopPropagation(); event.dataTransfer.dropEffect = "move"; setOver(true)
    }}
    onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOver(false) }}
    onDrop={(event) => {
      if (!enabled || !isLibraryDrag(event)) return
      event.preventDefault(); event.stopPropagation(); setOver(false)
      const keys = libraryDragKeys(event)
      if (keys.length) onDropContents(keys)
    }}>
    {children}
  </div>
}
