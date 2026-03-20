/**
 * Drag-and-drop file handling hook.
 */
import { useCallback, useState, type DragEvent } from "react"

interface UseDropZoneOptions {
  accept?: string[]
  onDrop: (files: File[]) => void
}

export function useDropZone({ accept, onDrop }: UseDropZoneOptions) {
  const [isDragging, setIsDragging] = useState(false)

  const handleDragEnter = useCallback((e: DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setIsDragging(true)
  }, [])

  const handleDragOver = useCallback((e: DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
  }, [])

  const handleDragLeave = useCallback((e: DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    // Only set false if leaving the actual drop zone (not a child)
    if (e.currentTarget === e.target) {
      setIsDragging(false)
    }
  }, [])

  const handleDrop = useCallback(
    (e: DragEvent) => {
      e.preventDefault()
      e.stopPropagation()
      setIsDragging(false)

      const files = Array.from(e.dataTransfer.files)
      if (files.length === 0) return

      if (accept && accept.length > 0) {
        const filtered = files.filter((f) =>
          accept.some((a) => {
            if (a.endsWith("/*")) return f.type.startsWith(a.replace("/*", "/"))
            return f.type === a
          }),
        )
        if (filtered.length > 0) onDrop(filtered)
      } else {
        onDrop(files)
      }
    },
    [accept, onDrop],
  )

  return {
    isDragging,
    dropZoneProps: {
      onDragEnter: handleDragEnter,
      onDragOver: handleDragOver,
      onDragLeave: handleDragLeave,
      onDrop: handleDrop,
    },
  }
}
