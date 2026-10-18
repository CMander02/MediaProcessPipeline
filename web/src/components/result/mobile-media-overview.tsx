import { useState, type ReactNode } from "react"
import { HugeiconsIcon } from "@hugeicons/react"
import { ArrowDown01Icon, ArrowUp01Icon } from "@hugeicons/core-free-icons"
import { useArchivePage } from "@/hooks/use-archive-page"
import { archiveQueryFilters, useLibraryControls } from "@/hooks/use-library-controls"
import type { useResultViewer } from "@/hooks/use-result-viewer"
import { api } from "@/lib/api"
import { formatDuration } from "@/lib/format"
import { buildHash, navigate, useRoute } from "@/lib/router"
import type { ArchiveItem } from "@/repositories/archive-types"
import { PlatformIcon } from "@/components/platform-icon"
import { SummaryTab } from "./summary-tab"

export function MobileMediaOverview({ view, actions }: { view: ReturnType<typeof useResultViewer>; actions: ReactNode }) {
  const [expanded, setExpanded] = useState(false)
  const { archive, displayTitle, platform, uploader, sourceHref, handleOpenSource, summary, mediaType, duration, isProcessing, taskError } = view
  const description = typeof archive?.metadata.description === "string" ? archive.metadata.description : ""
  return <>
    <section aria-label="内容简介" className="space-y-3 px-4 py-4">
      {(uploader || platform) && <div className="flex items-center gap-2 text-sm text-muted-foreground">
        {platform && <PlatformIcon platform={platform} iconOnly className="size-5" />}
        <span className="truncate">{uploader || platform}</span>
        {sourceHref && <button type="button" className="ml-auto min-h-10 shrink-0 px-2 text-xs" onClick={handleOpenSource}>查看原链接</button>}
      </div>}
      <div className="flex items-start gap-2">
        {view.editingTitle ? <input aria-label="内容标题" autoFocus className="min-w-0 flex-1 border-b bg-transparent py-1 text-base" value={view.titleDraft} onChange={(event) => view.setTitleDraft(event.target.value)} onBlur={view.commitTitle} onKeyDown={(event) => { if (event.key === "Enter") void view.commitTitle(); if (event.key === "Escape") view.setEditingTitle(false) }} /> : <button type="button" className="flex min-w-0 flex-1 items-start gap-2 text-left" aria-expanded={expanded} aria-label={expanded ? "收起简介" : "展开简介"} onClick={() => setExpanded((value) => !value)}>
          <h1 className={`min-w-0 flex-1 text-base font-semibold leading-7 ${expanded ? "" : "line-clamp-2"}`}>{displayTitle}</h1>
          <HugeiconsIcon icon={expanded ? ArrowUp01Icon : ArrowDown01Icon} className="mt-1.5 size-4 shrink-0 text-muted-foreground" />
        </button>}
        {actions}
      </div>
      <p className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
        <span>{mediaType === "video" ? "视频" : "音频"}</span>
        {(duration || archive?.duration_seconds) ? <span>{formatDuration(duration || archive?.duration_seconds || 0)}</span> : null}
        {archive?.date && <span>{archive.date}</span>}
        {isProcessing && <span>处理中</span>}
      </p>
      {expanded && description && <p className="whitespace-pre-wrap break-words text-sm leading-7 text-muted-foreground">{description}</p>}
      {taskError && <p role="alert" className="break-words text-sm text-destructive">{taskError}</p>}
    </section>
    {summary && <details className="border-y">
      <summary className="cursor-pointer px-4 py-3 text-sm font-medium">内容摘要</summary>
      <SummaryTab content={summary} scrollable={false} />
    </details>}
    <MobileMediaQueue current={archive} />
  </>
}

function MobileMediaQueue({ current }: { current: ArchiveItem | null }) {
  const library = useLibraryControls()
  const route = useRoute()
  const [limit, setLimit] = useState(20)
  const scope = library.scope === "all" ? {} : library.folder ? { folder_id: library.folder } : { unclassified: true }
  const media = library.media.filter((value) => value !== "image").join(",") || "video,audio"
  const { archives, total, loading, error } = useArchivePage({ page: 1, page_size: limit, ...archiveQueryFilters(library), media, ...scope })
  const items = archives.filter((item) => (item.has_video || item.has_audio) && item.path !== current?.path && (!current?.content_key || item.content_key !== current.content_key))
  const open = (item: ArchiveItem) => {
    if (route.page === "files") {
      navigate(buildHash("files", { ...route.params, item: item.content_key || item.path, attempt: undefined, viewerTask: undefined, generation: undefined, play: 1 }), { replace: true })
    } else {
      navigate(buildHash("result/archive", { path: item.path, taskId: item.task_id, play: 1 }), { replace: true })
    }
  }
  return <section aria-label="继续浏览" className="pb-[max(1rem,env(safe-area-inset-bottom))]">
    <h2 className="px-4 pb-1 pt-4 text-sm font-semibold">继续浏览</h2>
    {items.map((item) => <button type="button" key={item.path} className="flex w-full items-start gap-3 border-b px-4 py-3 text-left active:bg-muted" onClick={() => open(item)}>
      <span className="relative aspect-[535/304] w-[38%] max-w-44 shrink-0 overflow-hidden rounded-md bg-muted">
        <img src={item.thumbnail_url ?? api.archives.thumbnailUrl(item.path)} alt="" loading="lazy" className="size-full object-cover" onError={(event) => { event.currentTarget.style.visibility = "hidden" }} />
        {item.duration_seconds ? <span className="absolute bottom-1 right-1 rounded bg-black/65 px-1 text-[11px] text-white">{formatDuration(item.duration_seconds)}</span> : null}
      </span>
      <span className="flex min-w-0 flex-1 flex-col gap-2">
        <span className="line-clamp-2 text-sm leading-5">{item.title}</span>
        <span className="line-clamp-1 text-xs text-muted-foreground">{typeof item.metadata.uploader === "string" ? item.metadata.uploader : item.has_video ? "视频" : "音频"}</span>
        <span className="text-xs text-muted-foreground">{item.date}</span>
      </span>
    </button>)}
    {loading && <p className="px-4 py-3 text-sm text-muted-foreground">正在加载…</p>}
    {error && <p role="alert" className="px-4 py-3 text-sm text-destructive">{error}</p>}
    {!loading && !error && !items.length && <p className="px-4 py-3 text-sm text-muted-foreground">当前范围没有其他音视频</p>}
    {archives.length < total && limit < 500 && <button type="button" className="min-h-11 w-full text-sm text-muted-foreground" onClick={() => setLimit((value) => Math.min(500, value + 20))}>加载更多</button>}
  </section>
}
