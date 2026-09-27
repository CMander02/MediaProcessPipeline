import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import type { IPureNode } from "markmap-common"
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { HugeiconsIcon } from "@hugeicons/react"
import { Gps01Icon, Maximize01Icon, HierarchyIcon, File02Icon } from "@hugeicons/core-free-icons"
import { mindmapMarkdownForReading, sanitizeMindmapMarkdown } from "@/lib/mindmap"
import { formatDuration } from "@/lib/format"
import { MarkdownRenderer } from "./markdown-renderer"
import type { TranscriptTocNode } from "./transcript-tab"

interface MindmapViewerProps {
  markdown: string
  /** When true, the SVG fills its parent container (for use as a tab panel).
   *  When false (default), renders as a compact preview with expand-to-dialog. */
  fillContainer?: boolean
  /** Override the root node label (defaults to whatever markmap generates). */
  title?: string
  /** Called with a fit() function once the markmap is ready (fillContainer mode). */
  onFitReady?: (fit: () => void) => void
  /** Chapters with start times (seconds); matching nodes jump there when their label is clicked. */
  chapters?: TranscriptTocNode[] | null
  /** Playback position in seconds, to highlight the chapter being played. */
  currentTime?: number
  onSeek?: (timeMs: number) => void
}

function plainText(html: string): string {
  const element = document.createElement("div")
  element.innerHTML = html
  return element.textContent ?? ""
}

/** Compare labels loosely: no markup, spaces, punctuation or case. */
function labelKey(value: string): string {
  return plainText(value).toLowerCase().replace(/[\s\p{P}\p{S}]+/gu, "")
}

/** Start time (seconds) of the chapter a node stands for, if any. */
function chapterStartFor(node: MindmapNode, chapters: TranscriptTocNode[]): number | null {
  const key = labelKey(node.content)
  if (key.length < 2) return null
  for (const chapter of chapters) {
    if (typeof chapter.start !== "number") continue
    const chapterKey = labelKey(chapter.title)
    if (!chapterKey) continue
    if (chapterKey === key) return chapter.start
    if (Math.min(chapterKey.length, key.length) >= 4 && (chapterKey.includes(key) || key.includes(chapterKey))) return chapter.start
  }
  return null
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

export function MindmapViewer({ markdown, fillContainer, title, onFitReady, chapters, currentTime, onSeek }: MindmapViewerProps) {
  const svgRef = useRef<SVGSVGElement>(null)
  const dialogSvgRef = useRef<SVGSVGElement>(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [viewMode, setViewMode] = useState<"mindmap" | "markdown">("mindmap")
  const [error, setError] = useState<string | null>(null)
  const [rootNode, setRootNode] = useState<MindmapNode | null>(null)
  const mmRef = useRef<MarkmapInstance | null>(null)
  const dialogMMRef = useRef<MarkmapInstance | null>(null)
  const displayMarkdown = useMemo(() => sanitizeMindmapMarkdown(markdown), [markdown])
  const readingMarkdown = useMemo(() => mindmapMarkdownForReading(markdown), [markdown])
  const onFitReadyRef = useRef(onFitReady)
  onFitReadyRef.current = onFitReady
  const chaptersRef = useRef<TranscriptTocNode[]>([])
  chaptersRef.current = (chapters ?? []).filter((chapter) => typeof chapter.start === "number")
  const onSeekRef = useRef(onSeek)
  onSeekRef.current = onSeek
  const currentChapter = useMemo(() => {
    if (!chapters?.length || currentTime === undefined) return null
    let current: TranscriptTocNode | null = null
    for (const chapter of chapters) {
      if (typeof chapter.start === "number" && chapter.start <= currentTime + 0.25) current = chapter
    }
    return current
  }, [chapters, currentTime])
  const currentChapterRef = useRef<TranscriptTocNode | null>(null)
  currentChapterRef.current = currentChapter

  useEffect(() => {
    let cancelled = false

    ;(async () => {
      try {
        const { Transformer } = await import("markmap-lib")
        const transformer = new Transformer()
        const { root } = transformer.transform(displayMarkdown)

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
  }, [displayMarkdown, title])

  const renderMarkmap = useCallback(
    async (
      svgEl: SVGSVGElement,
      ref: React.MutableRefObject<MarkmapInstance | null>,
      data: MindmapNode,
      isCancelled: () => boolean,
    ) => {
      try {
        const { Markmap } = await import("markmap-view")
        if (isCancelled()) return

        svgEl.innerHTML = ""
        ref.current?.destroy?.()

        // d3-zoom reads SVGAnimatedLength directly. Numeric presentation
        // attributes keep that lookup resolvable when the canvas itself is
        // sized responsively with Tailwind's percentage-based utilities.
        const viewport = svgEl.getBoundingClientRect()
        svgEl.setAttribute("width", String(Math.max(Math.round(viewport.width), 1)))
        svgEl.setAttribute("height", String(Math.max(Math.round(viewport.height), 1)))

        const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches
        ref.current = Markmap.create(
          svgEl,
          {
            autoFit: true,
            duration: reducedMotion ? 0 : 300,
            initialExpandLevel: 2,
            maxWidth: 300,
            // Leave a margin so the outermost labels are not cut at the edges.
            fitRatio: 0.88,
          },
          cloneMindmapNode(data),
        ) as unknown as MarkmapInstance

        const fit = ref.current.fit?.bind(ref.current)
        ref.current.fit = () => {
          if (!svgEl.isConnected) return
          const bounds = svgEl.getBoundingClientRect()
          if (bounds.width > 0 && bounds.height > 0) return fit?.()
        }

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

          // A chapter's label jumps to that point in the media; its circle still folds the branch.
          const start = onSeekRef.current ? chapterStartFor(node, chaptersRef.current) : null
          if (start !== null) {
            // Seeking keeps the current view; the label is highlighted once playback reaches it.
            onSeekRef.current?.(Math.max(0, Math.round(start * 1000)))
            return
          }

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

        const markChapters = () => {
          const chaptersNow = chaptersRef.current
          const current = currentChapterRef.current
          svgEl.querySelectorAll<SVGGElement & { __data__?: MindmapNode }>("g.markmap-node").forEach((group) => {
            const node = group.__data__
            const start = node && chaptersNow.length ? chapterStartFor(node, chaptersNow) : null
            group.classList.toggle("mpp-chapter-node", start !== null)
            group.classList.toggle("mpp-current-chapter", start !== null && current?.start === start)
            let tip = group.querySelector(":scope > title")
            if (start !== null && onSeekRef.current) {
              if (!tip) {
                tip = document.createElementNS("http://www.w3.org/2000/svg", "title")
                group.prepend(tip)
              }
              tip.textContent = `跳到 ${formatDuration(start)}`
            } else {
              tip?.remove()
            }
          })
        }
        let frame = 0
        const observer = new MutationObserver(() => {
          cancelAnimationFrame(frame)
          frame = requestAnimationFrame(markChapters)
        })
        observer.observe(svgEl, { childList: true, subtree: true })
        markChapters()
        ;(svgEl as SVGSVGElement & { __mppMarkChapters?: () => void }).__mppMarkChapters = markChapters

        const destroy = ref.current.destroy?.bind(ref.current)
        const selection = ref.current.svg as {
          interrupt: () => unknown
          selectAll: (selector: string) => { interrupt: () => unknown }
        }
        ref.current.destroy = () => {
          observer.disconnect()
          cancelAnimationFrame(frame)
          svgEl.removeEventListener("click", handleLabelClick)
          selection.interrupt()
          selection.selectAll("*").interrupt()
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
    if (viewMode !== "mindmap" || !svgRef.current || !rootNode) return
    let cancelled = false
    const el = svgRef.current

    ;(async () => {
      if (!cancelled) {
        await renderMarkmap(el, mmRef, rootNode, () => cancelled)
        if (cancelled) return
        if (!cancelled && fillContainer && onFitReadyRef.current) {
          onFitReadyRef.current(() => mmRef.current?.fit?.())
        }
      }
    })()

    return () => {
      cancelled = true
      mmRef.current?.destroy?.()
      mmRef.current = null
    }
  }, [rootNode, renderMarkmap, fillContainer, viewMode])

  useEffect(() => {
    if (viewMode !== "mindmap" || !dialogOpen || !dialogSvgRef.current || !rootNode) return
    let cancelled = false
    const timer = setTimeout(async () => {
      if (dialogSvgRef.current) {
        await renderMarkmap(dialogSvgRef.current, dialogMMRef, rootNode, () => cancelled)
      }
    }, 100)

    return () => {
      cancelled = true
      clearTimeout(timer)
      dialogMMRef.current?.destroy?.()
      dialogMMRef.current = null
    }
  }, [dialogOpen, rootNode, renderMarkmap, viewMode])

  useEffect(() => () => {
    mmRef.current?.destroy?.()
    dialogMMRef.current?.destroy?.()
  }, [])

  // Highlight the chapter being played.
  useEffect(() => {
    for (const svg of [svgRef.current, dialogSvgRef.current]) {
      (svg as (SVGSVGElement & { __mppMarkChapters?: () => void }) | null)?.__mppMarkChapters?.()
    }
  }, [currentChapter, chapters])

  if (!markdown) return null

  if (error) {
    return <p className="p-4 text-sm text-destructive">{error}</p>
  }

  const toggleView = () =>
    setViewMode((m) => (m === "mindmap" ? "markdown" : "mindmap"))

  // Button shows the icon of the *other* view — click to switch to it.
  const viewToggleLabel = viewMode === "mindmap" ? "切换到 Markdown" : "切换到思维导图"
  const ViewToggleButton = (
    <Button
      type="button"
      variant="ghost"
      size="icon-sm"
      onClick={toggleView}
      className="absolute right-2 top-2 z-10 bg-background/80 text-muted-foreground shadow-sm backdrop-blur-sm"
      title={viewToggleLabel}
      aria-label={viewToggleLabel}
    >
      <HugeiconsIcon icon={viewMode === "mindmap" ? File02Icon : HierarchyIcon} />
    </Button>
  )

  const MarkdownPane = (
    <ScrollArea className="h-full w-full">
      <article className="prose prose-sm dark:prose-invert max-w-none px-4 py-3">
        <MarkdownRenderer>{readingMarkdown}</MarkdownRenderer>
      </article>
    </ScrollArea>
  )

  if (fillContainer) {
    return (
      <div className="relative h-full w-full overflow-hidden rounded-lg border bg-card">
        {ViewToggleButton}
        {viewMode === "mindmap" ? (
          <svg ref={svgRef} className="h-full w-full" />
        ) : (
          MarkdownPane
        )}
      </div>
    )
  }

  return (
    <>
      <div className="relative h-[280px] overflow-hidden rounded-lg border bg-card transition-colors hover:border-primary/30">
        {ViewToggleButton}
        {viewMode === "mindmap" ? (
          <svg ref={svgRef} className="h-full w-full" />
        ) : (
          MarkdownPane
        )}
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          onClick={() => setDialogOpen(true)}
          className="absolute right-11 top-2 bg-background/80 text-muted-foreground shadow-sm backdrop-blur-sm"
          title="展开导图"
          aria-label="展开导图"
        >
          <HugeiconsIcon icon={Maximize01Icon} />
        </Button>
      </div>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent
          className="flex h-[90vh] flex-col gap-0 p-0 sm:max-w-[95vw]"
          showCloseButton
        >
          <DialogTitle className="sr-only">{title || "思维导图"}</DialogTitle>
          <div className="shrink-0 border-b px-4 py-2">
            <div className="flex items-center justify-end gap-1">
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                onClick={toggleView}
                className="text-muted-foreground"
                title={viewToggleLabel}
                aria-label={viewToggleLabel}
              >
                <HugeiconsIcon icon={viewMode === "mindmap" ? File02Icon : HierarchyIcon} />
              </Button>
              {viewMode === "mindmap" && (
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  onClick={() => dialogMMRef.current?.fit?.()}
                  className="text-muted-foreground"
                  title="回正视角"
                  aria-label="回正视角"
                >
                  <HugeiconsIcon icon={Gps01Icon} />
                </Button>
              )}
            </div>
          </div>
          <div className="min-h-0 flex-1 bg-card">
            {viewMode === "mindmap" ? (
              <svg ref={dialogSvgRef} className="h-full w-full" />
            ) : (
              MarkdownPane
            )}
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}
