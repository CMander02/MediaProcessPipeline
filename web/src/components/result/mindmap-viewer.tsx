import { useCallback, useEffect, useRef, useState } from "react"
import type { IPureNode } from "markmap-common"
import { Dialog, DialogContent } from "@/components/ui/dialog"
import { HugeiconsIcon } from "@hugeicons/react"
import { Gps01Icon, Maximize01Icon } from "@hugeicons/core-free-icons"

interface MindmapViewerProps {
  markdown: string
  /** When true, the SVG fills its parent container (for use as a tab panel).
   *  When false (default), renders as a compact preview with expand-to-dialog. */
  fillContainer?: boolean
  /** Override the root node label (defaults to whatever markmap generates). */
  title?: string
  /** Called with a fit() function once the markmap is ready (fillContainer mode). */
  onFitReady?: (fit: () => void) => void
}

interface NodeRect {
  x: number
  y: number
  width: number
  height: number
}

interface RenderState {
  id: number
  path: string
  rect: NodeRect
}

interface MindmapNode extends IPureNode {
  children: MindmapNode[]
  payload?: Record<string, unknown>
  state?: RenderState
}

interface MarkmapInstance {
  destroy?: () => void
  fit?: () => Promise<void> | void
  toggleNode?: (node: MindmapNode, recursive?: boolean) => Promise<void>
  rescale?: (scale: number) => Promise<void>
  handleClick?: (event: MouseEvent, node: MindmapNode) => void
  state?: { data?: MindmapNode }
  svg?: unknown
  zoom?: unknown
}

function cloneMindmapNode(node: MindmapNode): MindmapNode {
  return {
    content: node.content,
    payload: node.payload ? { ...node.payload } : undefined,
    children: node.children.map(cloneMindmapNode),
  }
}

function getNodeFocusPoint(node: MindmapNode) {
  const rect = node.state?.rect
  if (!rect) return null
  return {
    x: rect.x + rect.width,
    y: rect.y + rect.height / 2,
  }
}

function walkNodes(node: MindmapNode, visit: (current: MindmapNode) => void) {
  visit(node)
  node.children.forEach((child) => walkNodes(child, visit))
}

function findNodeByPath(root: MindmapNode, path: string): MindmapNode | null {
  let found: MindmapNode | null = null
  walkNodes(root, (node) => {
    if (node.state?.path === path) {
      found = node
    }
  })
  return found
}

function collectFocusNodes(root: MindmapNode, target: MindmapNode): MindmapNode[] {
  const nodes = new Map<string, MindmapNode>()

  if (target.state?.path) {
    nodes.set(target.state.path, target)
  }

  target.children.forEach((child: MindmapNode) => {
    if (child.state?.path) {
      nodes.set(child.state.path, child)
    }
  })

  return Array.from(nodes.values())
}


async function focusBranch(mm: MarkmapInstance, target: MindmapNode) {
  const root = mm.state?.data
  const svgSelection = mm.svg as
    | {
        node: () => SVGSVGElement | null
        call: (fn: unknown, arg: unknown) => unknown
      }
    | undefined
  const zoomBehavior = mm.zoom as { transform?: unknown } | undefined

  if (!root || !svgSelection || !zoomBehavior?.transform) return

  const liveTarget = target.state?.path ? findNodeByPath(root, target.state.path) : null
  const focusTarget = liveTarget ?? target
  if (!focusTarget.state?.rect) return

  const focusNodes = collectFocusNodes(root, focusTarget).filter((node) => node.state?.rect)
  if (!focusNodes.length) return

  const center = getNodeFocusPoint(focusTarget)
  const svgNode = svgSelection.node()
  if (!center || !svgNode) return

  // Only use target node + its direct children to compute the frame (no parent influence)
  const hasChildren = focusNodes.length > 1
  let leftSpan = 40
  let rightSpan = 60
  let topSpan = 40
  let bottomSpan = 40

  focusNodes.forEach((node) => {
    const rect = node.state?.rect
    if (!rect) return

    leftSpan = Math.max(leftSpan, center.x - rect.x)
    // Add extra right padding for child text nodes
    const extraRight = node.state?.path !== focusTarget.state?.path ? 40 : 0
    rightSpan = Math.max(rightSpan, rect.x + rect.width - center.x + extraRight)
    topSpan = Math.max(topSpan, center.y - rect.y)
    bottomSpan = Math.max(bottomSpan, rect.y + rect.height - center.y)
  })

  const frameWidth = leftSpan + rightSpan
  const frameHeight = topSpan + bottomSpan
  const { width, height } = svgNode.getBoundingClientRect()
  if (!width || !height) return

  // Place focus point (connection dot) at 38% from left, leaving 62% for child nodes
  const anchorRatioX = hasChildren ? 0.38 : 0.5
  const ratio = 0.85
  const targetScale = Math.min(
    (width * anchorRatioX * 2) / frameWidth * ratio,
    height / frameHeight * ratio,
    2.2,
  )
  const currentScale = Math.max(((svgNode as SVGSVGElement & { __zoom?: { k?: number } }).__zoom?.k ?? 1), 0.01)
  const scaleFactor = targetScale / currentScale

  if (Math.abs(scaleFactor - 1) > 0.01) {
    await mm.rescale?.(scaleFactor)
  }

  const updatedTransform = (svgNode as SVGSVGElement & {
    __zoom?: { k: number; x: number; y: number; translate: (dx: number, dy: number) => unknown }
  }).__zoom
  if (!updatedTransform) return

  const currentScreenX = center.x * updatedTransform.k + updatedTransform.x
  const currentScreenY = center.y * updatedTransform.k + updatedTransform.y
  // Place the connection point at anchorRatioX horizontally, centered vertically
  const desiredScreenX = width * anchorRatioX
  const desiredScreenY = height / 2
  const deltaX = (desiredScreenX - currentScreenX) / updatedTransform.k
  const deltaY = (desiredScreenY - currentScreenY) / updatedTransform.k
  const translated = updatedTransform.translate(deltaX, deltaY)

  await Promise.resolve(svgSelection.call(zoomBehavior.transform, translated))
}

export function MindmapViewer({ markdown, fillContainer, title, onFitReady }: MindmapViewerProps) {
  const svgRef = useRef<SVGSVGElement>(null)
  const dialogSvgRef = useRef<SVGSVGElement>(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [rootNode, setRootNode] = useState<MindmapNode | null>(null)
  const mmRef = useRef<MarkmapInstance | null>(null)
  const dialogMMRef = useRef<MarkmapInstance | null>(null)
  const onFitReadyRef = useRef(onFitReady)
  onFitReadyRef.current = onFitReady

  useEffect(() => {
    let cancelled = false

    ;(async () => {
      try {
        const { Transformer } = await import("markmap-lib")
        const transformer = new Transformer()
        const { root } = transformer.transform(markdown)

        if (cancelled) return

        const cloned = cloneMindmapNode(root as MindmapNode)
        if (title) cloned.content = title
        setRootNode(cloned)
        setError(null)
      } catch (e) {
        if (cancelled) return
        setRootNode(null)
        setError(String(e))
      }
    })()

    return () => {
      cancelled = true
    }
  }, [markdown, title])

  const renderMarkmap = useCallback(
    async (
      svgEl: SVGSVGElement,
      ref: React.MutableRefObject<MarkmapInstance | null>,
      data: MindmapNode,
    ) => {
      try {
        const { Markmap } = await import("markmap-view")

        svgEl.innerHTML = ""
        ref.current?.destroy?.()

        ref.current = Markmap.create(
          svgEl,
          {
            autoFit: true,
            duration: 300,
            initialExpandLevel: 2,
            maxWidth: 300,
          },
          cloneMindmapNode(data),
        ) as unknown as MarkmapInstance

        ref.current.handleClick = (event: MouseEvent, node: MindmapNode) => {
          event.preventDefault()
          event.stopPropagation()

          const recursive = navigator.platform.includes("Mac")
            ? event.metaKey
            : event.ctrlKey

          void ref.current?.toggleNode?.(node, recursive).then(() => {
            if (ref.current) {
              void focusBranch(ref.current, node)
            }
          })
        }

        const handleLabelClick = (event: MouseEvent) => {
          const target = event.target as Element | null
          if (!target || target.closest("circle")) return

          const group = target.closest("g.markmap-node") as
            | (SVGGElement & { __data__?: MindmapNode })
            | null
          const node = group?.__data__
          if (!node) return

          event.preventDefault()
          event.stopPropagation()

          const recursive = navigator.platform.includes("Mac")
            ? event.metaKey
            : event.ctrlKey

          void ref.current?.toggleNode?.(node, recursive).then(() => {
            if (ref.current) {
              void focusBranch(ref.current, node)
            }
          })
        }

        svgEl.addEventListener("click", handleLabelClick)

        const destroy = ref.current.destroy?.bind(ref.current)
        ref.current.destroy = () => {
          svgEl.removeEventListener("click", handleLabelClick)
          destroy?.()
        }

        setError(null)
      } catch (e) {
        setError(String(e))
      }
    },
    [],
  )

  useEffect(() => {
    if (!svgRef.current || !rootNode) return
    let cancelled = false
    const el = svgRef.current

    ;(async () => {
      if (!cancelled) {
        await renderMarkmap(el, mmRef, rootNode)
        if (!cancelled && fillContainer && onFitReadyRef.current) {
          onFitReadyRef.current(() => mmRef.current?.fit?.())
        }
      }
    })()

    return () => {
      cancelled = true
    }
  }, [rootNode, renderMarkmap, fillContainer])

  useEffect(() => {
    if (!dialogOpen || !dialogSvgRef.current || !rootNode) return
    const timer = setTimeout(() => {
      if (dialogSvgRef.current) {
        renderMarkmap(dialogSvgRef.current, dialogMMRef, rootNode)
      }
    }, 100)

    return () => clearTimeout(timer)
  }, [dialogOpen, rootNode, renderMarkmap])

  useEffect(() => () => {
    mmRef.current?.destroy?.()
    dialogMMRef.current?.destroy?.()
  }, [])

  if (!markdown) return null

  if (error) {
    return <p className="p-4 text-sm text-destructive">{error}</p>
  }

  if (fillContainer) {
    return (
      <div className="relative h-full w-full overflow-hidden rounded-lg border bg-card">
        <svg ref={svgRef} className="h-full w-full" />
      </div>
    )
  }

  return (
    <>
      <div className="relative h-[280px] overflow-hidden rounded-lg border bg-card transition-colors hover:border-primary/30">
        <svg ref={svgRef} className="h-full w-full" />
        <button
          onClick={() => setDialogOpen(true)}
          className="absolute right-2 top-2 rounded-md bg-background/80 p-1.5 text-muted-foreground shadow-sm backdrop-blur-sm transition-colors hover:bg-muted hover:text-foreground"
          title="展开导图"
        >
          <HugeiconsIcon icon={Maximize01Icon} className="h-4 w-4" />
        </button>
      </div>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent
          className="flex h-[90vh] flex-col gap-0 p-0 sm:max-w-[95vw]"
          showCloseButton
        >
          <div className="shrink-0 border-b px-4 py-2">
            <div className="flex items-center justify-end">
              <button
                onClick={() => dialogMMRef.current?.fit?.()}
                className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                title="回正视角"
              >
                <HugeiconsIcon icon={Gps01Icon} className="h-4 w-4" />
              </button>
            </div>
          </div>
          <div className="min-h-0 flex-1 bg-card">
            <svg ref={dialogSvgRef} className="h-full w-full" />
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}
