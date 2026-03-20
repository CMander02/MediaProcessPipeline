import { useCallback, useEffect, useRef, useState } from "react"
import {
  Dialog,
  DialogContent,
} from "@/components/ui/dialog"
import { Maximize2, ZoomIn, ZoomOut, LocateFixed } from "lucide-react"

interface MindmapViewerProps {
  markdown: string
}

export function MindmapViewer({ markdown }: MindmapViewerProps) {
  const inlineSvgRef = useRef<SVGSVGElement>(null)
  const fullSvgRef = useRef<SVGSVGElement>(null)
  const [fullscreen, setFullscreen] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const inlineMMRef = useRef<{ destroy?: () => void; fit?: () => void } | null>(null)
  const fullMMRef = useRef<{ destroy?: () => void; fit?: () => void; rescale?: (scale: number) => void } | null>(null)

  // Render into an SVG element
  const renderMarkmap = useCallback(
    async (svgEl: SVGSVGElement, mmRef: React.MutableRefObject<{ destroy?: () => void; fit?: () => void } | null>) => {
      try {
        const { Transformer } = await import("markmap-lib")
        const { Markmap } = await import("markmap-view")

        const transformer = new Transformer()
        const { root } = transformer.transform(markdown)

        svgEl.innerHTML = ""
        if (mmRef.current?.destroy) mmRef.current.destroy()

        mmRef.current = Markmap.create(svgEl, {
          autoFit: true,
          duration: 300,
        }, root) as unknown as { destroy?: () => void; fit?: () => void }

        setError(null)
      } catch (e) {
        setError(String(e))
      }
    },
    [markdown],
  )

  // Render inline
  useEffect(() => {
    if (!inlineSvgRef.current || !markdown) return
    let cancelled = false
    const svgEl = inlineSvgRef.current
    ;(async () => {
      if (!cancelled) await renderMarkmap(svgEl, inlineMMRef)
    })()
    return () => { cancelled = true }
  }, [markdown, renderMarkmap])

  // Render fullscreen (when dialog opens)
  useEffect(() => {
    if (!fullscreen || !fullSvgRef.current || !markdown) return
    // Small delay to let dialog mount and have dimensions
    const timer = setTimeout(() => {
      if (fullSvgRef.current) renderMarkmap(fullSvgRef.current, fullMMRef)
    }, 100)
    return () => clearTimeout(timer)
  }, [fullscreen, markdown, renderMarkmap])

  const handleFit = () => {
    if (fullMMRef.current?.fit) fullMMRef.current.fit()
  }

  if (!markdown) return null

  return (
    <>
      {/* Inline preview */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
            思维导图
          </h3>
          <button
            onClick={() => setFullscreen(true)}
            className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
            title="全屏查看"
          >
            <Maximize2 className="w-3.5 h-3.5" />
            <span>展开</span>
          </button>
        </div>
        {error ? (
          <p className="text-sm text-destructive">{error}</p>
        ) : (
          <div
            className="rounded-lg border bg-card overflow-hidden h-[280px] cursor-pointer hover:border-primary/30 transition-colors"
            onClick={() => setFullscreen(true)}
          >
            <svg ref={inlineSvgRef} className="w-full h-full" />
          </div>
        )}
      </div>

      {/* Fullscreen dialog */}
      <Dialog open={fullscreen} onOpenChange={setFullscreen}>
        <DialogContent
          className="sm:max-w-[95vw] h-[90vh] flex flex-col gap-0 p-0"
          showCloseButton
        >
          {/* Toolbar */}
          <div className="shrink-0 flex items-center justify-between px-4 py-2 border-b">
            <h3 className="text-sm font-medium">思维导图</h3>
            <div className="flex items-center gap-1">
              <button
                onClick={handleFit}
                className="rounded-md p-1.5 text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                title="适应窗口"
              >
                <LocateFixed className="h-4 w-4" />
              </button>
            </div>
          </div>

          {/* Mindmap area */}
          <div className="flex-1 min-h-0 bg-card">
            <svg ref={fullSvgRef} className="w-full h-full" />
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}
