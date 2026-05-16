import { useState, useEffect, useCallback } from "react"
import { Button } from "@/components/ui/button"
import { HugeiconsIcon } from "@hugeicons/react"
import { ArrowLeft01Icon, ArrowRight01Icon, Loading03Icon } from "@hugeicons/core-free-icons"
import { api } from "@/lib/api"

export interface ImageDescription {
  index: number
  image_path: string
  kind: string
  text: string
}

interface Props {
  archivePath: string
  descriptions: ImageDescription[]
  onImageIndexChange?: (index: number) => void
  isProcessing?: boolean
}

export function ImageNoteViewer({ archivePath, descriptions, onImageIndexChange, isProcessing }: Props) {
  const [currentIdx, setCurrentIdx] = useState(0)
  const [imgError, setImgError] = useState(false)

  const total = descriptions.length
  const current = descriptions[currentIdx]

  const go = useCallback((delta: number) => {
    setCurrentIdx((prev) => {
      const next = Math.max(0, Math.min(total - 1, prev + delta))
      onImageIndexChange?.(next)
      return next
    })
    setImgError(false)
  }, [total, onImageIndexChange])

  useEffect(() => {
    setImgError(false)
  }, [currentIdx])

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

  return (
    <div className="flex h-full flex-col">
      {/* Image display */}
      <div className="relative flex-1 min-h-0 flex items-center justify-center bg-muted/30 overflow-hidden">
        {imgUrl && !imgError ? (
          <img
            src={imgUrl}
            alt={`图片 ${currentIdx + 1}`}
            className="max-h-full max-w-full object-contain"
            onError={() => setImgError(true)}
          />
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
    </div>
  )
}
