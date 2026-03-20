import { useCallback, useEffect, useRef, useState } from "react"
import {
  Dialog,
  DialogContent,
} from "@/components/ui/dialog"
import { Maximize2, LocateFixed } from "lucide-react"

interface MindmapViewerProps {
  markdown: string
  /** When true, the SVG fills its parent container (for use as a tab panel).
   *  When false (default), renders as a compact preview with expand-to-dialog. */
  fillContainer?: boolean
}

export function MindmapViewer({ markdown, fillContainer }: MindmapViewerProps) {
  const svgRef = useRef<SVGSVGElement>(null)
  const dialogSvgRef = useRef<SVGSVGElement>(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const mmRef = useRef<{ destroy?: () => void; fit?: () => void } | null>(null)
  const dialogMMRef = useRef<{ destroy?: () => void; fit?: () => void } | null>(null)

  const renderMarkmap = useCallback(
    async (svgEl: SVGSVGElement, ref: React.MutableRefObject<{ destroy?: () => void; fit?: () => void } | null>) => {
      try {
        const { Transformer } = await import("markmap-lib")
        const { Markmap } = await import("markmap-view")

        const transformer = new Transformer()
        const { root } = transformer.transform(markdown)

        svgEl.innerHTML = ""
        if (ref.current?.destroy) ref.current.destroy()

        ref.current = Markmap.create(svgEl, {
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

  // Render main SVG
  useEffect(() => {
    if (!svgRef.current || !markdown) return
    let cancelled = false
    const el = svgRef.current
    ;(async () => {
      if (!cancelled) await renderMarkmap(el, mmRef)
    })()
    return () => { cancelled = true }
  }, [markdown, renderMarkmap])

  // Render dialog SVG
  useEffect(() => {
    if (!dialogOpen || !dialogSvgRef.current || !markdown) return
    const timer = setTimeout(() => {
      if (dialogSvgRef.current) renderMarkmap(dialogSvgRef.current, dialogMMRef)
    }, 100)
    return () => clearTimeout(timer)
  }, [dialogOpen, markdown, renderMarkmap])

  if (!markdown) return null

  if (error) {
    return <p className="text-sm text-destructive p-4">{error}</p>
  }

  // Full container mode — fill parent, no wrapper chrome
  if (fillContainer) {
    return (
      <div className="h-full w-full flex flex-col">
        <div className="shrink-0 flex items-center justify-between px-1 pb-2">
          <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
            思维导图
          </h3>
          <button
            onClick={() => mmRef.current?.fit?.()}
            className="rounded-md p-1 text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
            title="适应窗口"
          >
            <LocateFixed className="h-3.5 w-3.5" />
          </button>
        </div>
        <div className="flex-1 min-h-0 rounded-lg border bg-card overflow-hidden">
          <svg ref={svgRef} className="w-full h-full" />
        </div>
      </div>
    )
  }

  // Compact preview mode — fixed height, click to open dialog
  return (
    <>
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
            思维导图
          </h3>
          <button
            onClick={() => setDialogOpen(true)}
            className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
            title="全屏查看"
          >
            <Maximize2 className="w-3.5 h-3.5" />
            <span>展开</span>
          </button>
        </div>
        <div
          className="rounded-lg border bg-card overflow-hidden h-[280px] cursor-pointer hover:border-primary/30 transition-colors"
          onClick={() => setDialogOpen(true)}
        >
          <svg ref={svgRef} className="w-full h-full" />
        </div>
      </div>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent
          className="sm:max-w-[95vw] h-[90vh] flex flex-col gap-0 p-0"
          showCloseButton
        >
          <div className="shrink-0 flex items-center justify-between px-4 py-2 border-b">
            <h3 className="text-sm font-medium">思维导图</h3>
            <button
              onClick={() => dialogMMRef.current?.fit?.()}
              className="rounded-md p-1.5 text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
              title="适应窗口"
            >
              <LocateFixed className="h-4 w-4" />
            </button>
          </div>
          <div className="flex-1 min-h-0 bg-card">
            <svg ref={dialogSvgRef} className="w-full h-full" />
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}
