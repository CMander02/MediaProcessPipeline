import { useState, useEffect, useCallback } from "react"
import { Button } from "@/components/ui/button"
import { HugeiconsIcon } from "@hugeicons/react"
import { ArrowLeft01Icon, ArrowRight01Icon, Loading03Icon } from "@hugeicons/core-free-icons"
import { api } from "@/lib/api"
import { ImageLightbox, type LightboxImage } from "./image-lightbox"

export interface ImageDescription {
  index: number
  image_path: string
  kind: string
  text: string
}

interface Props {
  archivePath: string
  descriptions: ImageDescription[]
  activeIndex?: number
  onImageIndexChange?: (index: number) => void
  isProcessing?: boolean
}

export function ImageNoteViewer({ archivePath, descriptions, activeIndex, onImageIndexChange, isProcessing }: Props) {
  const [currentIdx, setCurrentIdx] = useState(0)
  const [imgError, setImgError] = useState(false)
  const [lightboxOpen, setLightboxOpen] = useState(false)

  const total = descriptions.length
  const current = descriptions[currentIdx]

  const setImageIndex = useCallback((nextIndex: number) => {
    const next = Math.max(0, Math.min(total - 1, nextIndex))
    setCurrentIdx(next)
    onImageIndexChange?.(descriptions[next]?.index ?? next)
    setImgError(false)
  }, [descriptions, total, onImageIndexChange])

  const go = useCallback((delta: number) => {
    setImageIndex(currentIdx + delta)
  }, [currentIdx, setImageIndex])

  useEffect(() => {
    setImgError(false)
  }, [currentIdx])

  useEffect(() => {
    if (typeof activeIndex !== "number" || total <= 0) return
    const byDescriptionIndex = descriptions.findIndex((item) => item.index === activeIndex)
    const next = byDescriptionIndex >= 0 ? byDescriptionIndex : activeIndex
    if (next >= 0 && next < total && next !== currentIdx) {
      setCurrentIdx(next)
      setImgError(false)
    }
  }, [activeIndex, currentIdx, descriptions, total])

  if (isProcessing && total === 0) {
    return (
      <div className="flex h-full items-center justify-center text-muted-foreground">
        <HugeiconsIcon icon={Loading03Icon} className="h-6 w-6 animate-spin mr-2" />
        <span className="text-sm">正在分析图片...</span>
      </div>
    )
  }

  if (total === 0) {
    return (
      <div className="flex h-full items-center justify-center text-muted-foreground text-sm">
        无图片数据
      </div>
    )
  }

  const imgUrl = current ? api.filesystem.mediaUrl(current.image_path) : null
  const lightboxImages: LightboxImage[] = descriptions
    .filter((item) => item.image_path)
    .map((item) => ({
      src: api.filesystem.mediaUrl(item.image_path),
      alt: `图片 ${item.index + 1}`,
    }))

  return (
    <div className="flex h-full flex-col">
      {/* Image display */}
      <div className="relative flex-1 min-h-0 flex items-center justify-center bg-muted/30 overflow-hidden">
        {imgUrl && !imgError ? (
          <button
            type="button"
            className="flex h-full w-full items-center justify-center"
            onClick={() => setLightboxOpen(true)}
            title="查看大图"
          >
            <img
              src={imgUrl}
              alt={`图片 ${currentIdx + 1}`}
              className="max-h-full max-w-full object-contain"
              onError={() => setImgError(true)}
            />
          </button>
        ) : (
          <div className="text-muted-foreground text-sm">图片加载失败</div>
        )}
      </div>

      {/* Navigation bar */}
      {total > 1 && (
        <div className="shrink-0 flex items-center justify-between gap-2 px-3 py-2 border-t bg-background">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => go(-1)}
            disabled={currentIdx === 0}
          >
            <HugeiconsIcon icon={ArrowLeft01Icon} className="h-4 w-4" />
          </Button>
          <span className="text-xs text-muted-foreground tabular-nums">
            {currentIdx + 1} / {total}
          </span>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => go(1)}
            disabled={currentIdx === total - 1}
          >
            <HugeiconsIcon icon={ArrowRight01Icon} className="h-4 w-4" />
          </Button>
        </div>
      )}
      <ImageLightbox
        images={lightboxImages}
        index={currentIdx}
        open={lightboxOpen}
        onIndexChange={setImageIndex}
        onOpenChange={setLightboxOpen}
      />
    </div>
  )
}
