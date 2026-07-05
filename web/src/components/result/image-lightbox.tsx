import { useCallback, useEffect, useRef, useState } from "react"
import { HugeiconsIcon } from "@hugeicons/react"
import { ArrowLeft01Icon, ArrowRight01Icon, Cancel01Icon } from "@hugeicons/core-free-icons"
import { cn } from "@/lib/utils"

export interface LightboxImage {
  src: string
  alt?: string
}

interface ImageLightboxProps {
  images: LightboxImage[]
  index: number
  open: boolean
  onIndexChange: (index: number) => void
  onOpenChange: (open: boolean) => void
}

const MIN_SCALE = 0.5
const MAX_SCALE = 6

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value))
}

export function ImageLightbox({
  images,
  index,
  open,
  onIndexChange,
  onOpenChange,
}: ImageLightboxProps) {
  const [scale, setScale] = useState(1)
  const [offset, setOffset] = useState({ x: 0, y: 0 })
  const [dragging, setDragging] = useState(false)
  const dragRef = useRef({ x: 0, y: 0, offsetX: 0, offsetY: 0 })
  const current = images[index]

  const resetView = useCallback(() => {
    setScale(1)
    setOffset({ x: 0, y: 0 })
    setDragging(false)
  }, [])

  const go = useCallback((delta: number) => {
    if (images.length <= 1) return
    const next = (index + delta + images.length) % images.length
    onIndexChange(next)
    resetView()
  }, [images.length, index, onIndexChange, resetView])

  useEffect(() => {
    if (!open) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onOpenChange(false)
      if (event.key === "ArrowLeft") go(-1)
      if (event.key === "ArrowRight") go(1)
    }
    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [go, onOpenChange, open])

  useEffect(() => {
    if (open) resetView()
  }, [index, open, resetView])

  if (!open || !current) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center overscroll-none bg-black/75"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onOpenChange(false)
      }}
    >
      <button
        type="button"
        className="absolute left-4 top-4 z-10 flex h-9 w-9 items-center justify-center rounded-full bg-background/90 text-foreground shadow-sm hover:bg-background"
        onClick={() => onOpenChange(false)}
        title="关闭"
      >
        <HugeiconsIcon icon={Cancel01Icon} className="h-5 w-5" />
      </button>

      {images.length > 1 && (
        <>
          <button
            type="button"
            className="absolute left-4 top-1/2 z-10 flex h-10 w-10 -translate-y-1/2 items-center justify-center rounded-full bg-background/90 text-foreground shadow-sm hover:bg-background"
            onClick={() => go(-1)}
            title="上一张"
          >
            <HugeiconsIcon icon={ArrowLeft01Icon} className="h-5 w-5" />
          </button>
          <button
            type="button"
            className="absolute right-4 top-1/2 z-10 flex h-10 w-10 -translate-y-1/2 items-center justify-center rounded-full bg-background/90 text-foreground shadow-sm hover:bg-background"
            onClick={() => go(1)}
            title="下一张"
          >
            <HugeiconsIcon icon={ArrowRight01Icon} className="h-5 w-5" />
          </button>
        </>
      )}

      <div
        className={cn(
          "max-h-[92vh] max-w-[92vw] select-none",
          dragging ? "cursor-grabbing" : "cursor-grab",
        )}
        onWheel={(event) => {
          const next = clamp(scale + (event.deltaY < 0 ? 0.18 : -0.18), MIN_SCALE, MAX_SCALE)
          setScale(next)
        }}
        onMouseDown={(event) => {
          event.preventDefault()
          dragRef.current = {
            x: event.clientX,
            y: event.clientY,
            offsetX: offset.x,
            offsetY: offset.y,
          }
          setDragging(true)
        }}
        onMouseMove={(event) => {
          if (!dragging) return
          const start = dragRef.current
          setOffset({
            x: start.offsetX + event.clientX - start.x,
            y: start.offsetY + event.clientY - start.y,
          })
        }}
        onMouseUp={() => setDragging(false)}
        onMouseLeave={() => setDragging(false)}
        onDoubleClick={resetView}
      >
        <img
          src={current.src}
          alt={current.alt ?? ""}
          className="max-h-[92vh] max-w-[92vw] object-contain shadow-2xl"
          draggable={false}
          style={{
            transform: `translate(${offset.x}px, ${offset.y}px) scale(${scale})`,
            transition: dragging ? "none" : "transform 120ms ease-out",
          }}
        />
      </div>

      {images.length > 1 && (
        <div className="absolute bottom-4 rounded-full bg-background/90 px-3 py-1 text-xs text-foreground shadow-sm">
          {index + 1} / {images.length}
        </div>
      )}
    </div>
  )
}
