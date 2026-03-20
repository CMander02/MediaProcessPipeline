import { useEffect, useRef, useState } from "react"
import { Maximize2, Minimize2 } from "lucide-react"

interface MindmapViewerProps {
  markdown: string
}

export function MindmapViewer({ markdown }: MindmapViewerProps) {
  const svgRef = useRef<SVGSVGElement>(null)
  const [expanded, setExpanded] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const markmapRef = useRef<unknown>(null)

  useEffect(() => {
    if (!svgRef.current || !markdown) return

    let cancelled = false

    async function render() {
      try {
        const { Transformer } = await import("markmap-lib")
        const { Markmap } = await import("markmap-view")

        if (cancelled || !svgRef.current) return

        const transformer = new Transformer()
        const { root } = transformer.transform(markdown)

        // Clear previous content
        svgRef.current.innerHTML = ""

        if (markmapRef.current) {
          // Dispose previous instance if it has a destroy method
          const prev = markmapRef.current as { destroy?: () => void }
          prev.destroy?.()
        }

        markmapRef.current = Markmap.create(svgRef.current, {
          autoFit: true,
          duration: 300,
        }, root)

        setError(null)
      } catch (e) {
        if (!cancelled) setError(String(e))
      }
    }

    render()
    return () => { cancelled = true }
  }, [markdown])

  if (!markdown) return null

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
          思维导图
        </h3>
        <button
          onClick={() => setExpanded(!expanded)}
          className="text-muted-foreground hover:text-foreground transition-colors"
        >
          {expanded ? <Minimize2 className="w-4 h-4" /> : <Maximize2 className="w-4 h-4" />}
        </button>
      </div>
      {error ? (
        <p className="text-sm text-destructive">{error}</p>
      ) : (
        <div
          className={`rounded-lg border bg-card overflow-hidden transition-all ${
            expanded ? "h-[600px]" : "h-[300px]"
          }`}
        >
          <svg ref={svgRef} className="w-full h-full" />
        </div>
      )}
    </div>
  )
}
