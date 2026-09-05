import { resolveOfflineFileUrl } from "@/repositories/archive-repository"
import { api } from "@/lib/api"
import { formatSourceDescriptionMarkdown } from "@/lib/source-description"
import { MarkdownRenderer } from "@/components/result/markdown-renderer"
import { useState } from "react"
import { ImageLightbox, type LightboxImage } from "@/components/result/image-lightbox"
import type { ReactNode } from "react"
import { type ImageDescription } from "@/components/result/image-note-viewer"
import { HugeiconsIcon } from "@hugeicons/react"
import { Loading03Icon } from "@hugeicons/core-free-icons"

function resolveNoteMediaSrc(src: string | undefined, archivePath: string, sep: string): string | undefined {
  if (!src) return src
  if (/^(?:https?:|data:|blob:)/i.test(src)) return src
  const normalized = src.replace(/\\/g, "/")
  const offlineRelative = normalized.match(/(?:^|\/)((?:images|descriptions)\/.*)$/i)?.[1] ?? normalized
  const offlineUrl = resolveOfflineFileUrl(archivePath, offlineRelative)
  if (offlineUrl) return offlineUrl
  if (/^[A-Za-z]:\//.test(normalized) || normalized.startsWith("/")) {
    return api.filesystem.mediaUrl(src)
  }
  return api.filesystem.mediaUrl(archivePath + sep + normalized.replace(/\//g, sep))
}

function mediaSource(path: string): string {
  return /^(?:https?:|data:|blob:)/i.test(path) ? path : api.filesystem.mediaUrl(path)
}

export function NoteMarkdown({ content, archivePath, sep }: { content: string; archivePath: string; sep: string }) {
  const displayContent = formatSourceDescriptionMarkdown(content)

  return (
    <div className="prose prose-sm max-w-none dark:prose-invert">
      <MarkdownRenderer
        components={{
          img: ({ src, alt }) => (
            <img
              src={resolveNoteMediaSrc(src, archivePath, sep)}
              alt={alt ?? ""}
              className="mx-auto my-4 max-h-[520px] w-full rounded-md object-contain"
              loading="lazy"
            />
          ),
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noreferrer">{children}</a>
          ),
        }}
      >
        {displayContent}
      </MarkdownRenderer>
    </div>
  )
}

type ArticleMarkdownSegment =
  | { kind: "markdown"; content: string }
  | { kind: "figure"; alt: string; src: string; caption: string | null }

function markdownHasInlineImages(content: string | null): boolean {
  if (!content) return false
  return /!\[[^\]]*]\([^)]+\)|<img\s/i.test(content)
}

function unescapeMarkdownText(value: string): string {
  return value.replace(/\\([\\[\]])/g, "$1")
}

function normalizeCaptionText(value: string): string {
  return unescapeMarkdownText(value).replace(/\s+/g, " ").trim()
}

function parseArticleMarkdownSegments(content: string): ArticleMarkdownSegment[] {
  const lines = content.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n")
  const segments: ArticleMarkdownSegment[] = []
  const pending: string[] = []

  const flushPending = () => {
    const markdown = pending.join("\n").trim()
    if (markdown) segments.push({ kind: "markdown", content: markdown })
    pending.length = 0
  }

  for (let i = 0; i < lines.length;) {
    const match = lines[i].match(/^!\[([^\]]*(?:\\][^\]]*)*)]\(([^)]+)\)\s*$/)
    if (!match) {
      pending.push(lines[i])
      i += 1
      continue
    }

    const alt = unescapeMarkdownText(match[1]).trim()
    const src = match[2].trim()
    let cursor = i + 1
    while (cursor < lines.length && lines[cursor].trim() === "") cursor += 1

    const captionStart = cursor
    const captionLines: string[] = []
    while (cursor < lines.length && lines[cursor].trim() !== "") {
      captionLines.push(lines[cursor])
      cursor += 1
    }
    const caption = captionLines.join("\n").trim()
    const shouldConsumeCaption =
      Boolean(caption) &&
      alt !== "图片" &&
      normalizeCaptionText(caption) === normalizeCaptionText(alt)

    flushPending()
    segments.push({
      kind: "figure",
      alt,
      src,
      caption: shouldConsumeCaption ? caption : null,
    })
    i = shouldConsumeCaption ? cursor : i + 1

    if (!shouldConsumeCaption && captionStart > i) {
      while (i < captionStart && lines[i]?.trim() === "") {
        pending.push(lines[i])
        i += 1
      }
    }
  }

  flushPending()
  return segments
}

function ArticleNoteMarkdown({ content, archivePath, sep }: { content: string; archivePath: string; sep: string }) {
  const segments = parseArticleMarkdownSegments(content)
  const [lightboxIndex, setLightboxIndex] = useState<number | null>(null)
  const figureSegments = segments.filter(
    (segment): segment is Extract<ArticleMarkdownSegment, { kind: "figure" }> => segment.kind === "figure",
  )
  const figureImages: LightboxImage[] = figureSegments
    .map((segment) => ({
      src: resolveNoteMediaSrc(segment.src, archivePath, sep) ?? segment.src,
      alt: segment.alt,
    }))
  const markdownComponents = {
    img: ({ src, alt }: { src?: string; alt?: string }) => (
      <img
        src={resolveNoteMediaSrc(src, archivePath, sep)}
        alt={alt ?? ""}
        className="mx-auto my-4 max-h-[520px] w-full rounded-md object-contain"
        loading="lazy"
      />
    ),
    a: ({ href, children }: { href?: string; children?: ReactNode }) => (
      <a href={href} target="_blank" rel="noreferrer">{children}</a>
    ),
  }

  return (
    <div className="prose prose-sm max-w-none dark:prose-invert">
      {segments.map((segment, index) => {
        if (segment.kind === "markdown") {
          return (
            <MarkdownRenderer key={`markdown-${index}`} components={markdownComponents}>
              {segment.content}
            </MarkdownRenderer>
          )
        }
        const currentFigureIndex = figureSegments.indexOf(segment)
        return (
          <figure key={`figure-${index}`} className="my-5">
            <button
              type="button"
              className="block w-full"
              onClick={() => setLightboxIndex(currentFigureIndex)}
              title="查看大图"
            >
              <img
                src={resolveNoteMediaSrc(segment.src, archivePath, sep)}
                alt={segment.alt}
                className="mx-auto max-h-[640px] w-full rounded-md object-contain"
                loading="lazy"
              />
            </button>
            {segment.caption ? (
              <figcaption className="mx-auto mt-2 max-w-2xl text-center text-xs leading-6 text-muted-foreground [&_a]:underline [&_code]:rounded [&_code]:bg-muted [&_code]:px-1 [&_p]:m-0">
                <MarkdownRenderer
                  components={{
                    a: ({ href, children }) => (
                      <a href={href} target="_blank" rel="noreferrer">{children}</a>
                    ),
                  }}
                >
                  {segment.caption}
                </MarkdownRenderer>
              </figcaption>
            ) : null}
          </figure>
        )
      })}
      <ImageLightbox
        images={figureImages}
        index={lightboxIndex ?? 0}
        open={lightboxIndex !== null}
        onIndexChange={setLightboxIndex}
        onOpenChange={(open) => {
          if (!open) setLightboxIndex(null)
        }}
      />
    </div>
  )
}

export function ArticleNoteReader({
  content,
  archivePath,
  sep,
  descriptions,
  isProcessing,
}: {
  content: string | null
  archivePath: string
  sep: string
  descriptions: ImageDescription[]
  isProcessing?: boolean
}) {
  const showLocalImages = !markdownHasInlineImages(content)
  const localImages = showLocalImages ? descriptions.filter((item) => item.image_path) : []
  const [lightboxIndex, setLightboxIndex] = useState<number | null>(null)
  const lightboxImages: LightboxImage[] = localImages.map((item) => ({
    src: mediaSource(item.image_path),
    alt: `图片 ${item.index + 1}`,
  }))

  return (
    <div className="h-full overflow-y-auto rounded-lg border bg-background">
      <div className="mx-auto max-w-3xl px-6 py-6">
        {content ? (
          <ArticleNoteMarkdown content={content} archivePath={archivePath} sep={sep} />
        ) : isProcessing ? (
          <div className="flex h-40 items-center justify-center text-muted-foreground">
            <HugeiconsIcon icon={Loading03Icon} className="mr-2 h-4 w-4 animate-spin" />
            <span className="text-sm">等待正文...</span>
          </div>
        ) : (
          <div className="flex h-40 items-center justify-center text-sm text-muted-foreground">
            暂无正文
          </div>
        )}
        {localImages.length > 0 && (
          <div className="mt-6 space-y-6">
            {localImages.map((item) => (
              <figure key={item.index} className="space-y-2">
                <button
                  type="button"
                  className="block w-full"
                  onClick={() => setLightboxIndex(localImages.findIndex((image) => image.index === item.index))}
                  title="查看大图"
                >
                  <img
                    src={mediaSource(item.image_path)}
                    alt={`图片 ${item.index + 1}`}
                    className="mx-auto max-h-[640px] w-full rounded-md object-contain"
                    loading="lazy"
                  />
                </button>
                {item.text ? (
                  <figcaption className="whitespace-pre-wrap text-xs leading-6 text-muted-foreground">
                    {item.text}
                  </figcaption>
                ) : null}
              </figure>
            ))}
          </div>
        )}
      </div>
      <ImageLightbox
        images={lightboxImages}
        index={lightboxIndex ?? 0}
        open={lightboxIndex !== null}
        onIndexChange={setLightboxIndex}
        onOpenChange={(open) => {
          if (!open) setLightboxIndex(null)
        }}
      />
    </div>
  )
}
