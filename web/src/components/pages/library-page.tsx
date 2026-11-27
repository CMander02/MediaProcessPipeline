import { lazy, Suspense, useCallback, useEffect, useRef, useState, type ReactNode } from "react"
import { HugeiconsIcon } from "@hugeicons/react"
import { ArrowDown01Icon, ArrowLeft01Icon, ArrowRight01Icon, FolderOpenIcon, MoreHorizontalIcon } from "@hugeicons/core-free-icons"
import { FilesPage } from "./files-page"
import { useArchivePage } from "@/hooks/use-archive-page"
import { useListPosition } from "@/hooks/use-view-position"
import { archiveCacheScope } from "@/lib/archive-cache-scope"
import { useActiveTasks } from "@/hooks/use-active-tasks"
import { archiveQueryFilters, libraryStateFromParams, rememberLibraryArchive, useLibraryControls } from "@/hooks/use-library-controls"
import { useLibraryDirectory } from "@/hooks/use-library-directory"
import { useMediaQuery } from "@/hooks/use-media-query"
import { usePlatform } from "@/platform/use-platform"
import { createArchiveRepository } from "@/repositories/archive-repository"
import type { ArchiveItem } from "@/repositories/archive-types"
import type { LibraryFilters } from "@/lib/archive-filters"
import { resolveArchivePlatform } from "@/lib/archive-filters"
import { ArchiveCoverFallback } from "@/components/archive-cover-fallback"
import { api } from "@/lib/api"
import { buildHash, navigate, parseHash, useRoute } from "@/lib/router"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { EmptyState, LoadingState } from "@/components/ui/page-state"
import { LibraryBatchActions } from "@/components/library/library-batch-actions"
import { LibraryDropTarget } from "@/components/library/library-drop-target"
import { MoveContentsDialog } from "@/components/library/move-contents-dialog"
import { useLibraryMove, type OnLibraryMoved } from "@/hooks/use-library-move"
import { startLibraryDrag } from "@/lib/library-drag"
import type { LibraryFolder } from "@/lib/library-types"
import { useAppAccess } from "@/hooks/use-app-access-context"
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"

interface Props { filters: LibraryFilters; page: number; onPageChange: (page: number) => void }
const EMPTY_FOLDERS: LibraryFolder[] = []
const expandedFolders = new Map<string, Set<string>>()
const ResultPageComplete = lazy(() => import("./result-page-complete").then((module) => ({ default: module.ResultPageComplete })))
const ResultPageLive = lazy(() => import("./result-page-live").then((module) => ({ default: module.ResultPageLive })))
export function LibraryPage(props: Props) {
  const library = useLibraryControls()
  const { folders } = useLibraryDirectory()
  const all = library.scope === "all"
  const open = (archive: ArchiveItem) => {
    rememberLibraryArchive({ ...library, folder: all ? undefined : archive.folder_id || undefined }, archive)
    library.setLocation({ folder: all ? undefined : archive.folder_id || undefined, item: archive.content_key || archive.path })
  }
  if (library.view !== "split") return <FilesPage {...props} scope={library.scope} folderId={all ? undefined : library.folder} folders={folders} onFolderOpen={(folder) => library.setLocation({ folder, item: undefined })} />
  return <FilesSplitView filters={props.filters} onOpen={open} />
}

function SplitViewGroup({ folderId, all = false, filters, active, onOpen, onFirst, renderFolder, onMoved,
  selectionMode, selected, onSelect, onDragging }: {
  folderId?: string; all?: boolean; filters: LibraryFilters; active?: string; onOpen: (item: ArchiveItem) => void; onFirst?: (item: ArchiveItem) => void
  renderFolder?: (folder: LibraryFolder) => ReactNode; onMoved: OnLibraryMoved;
  selectionMode: boolean; selected: Set<string>; onSelect: (key: string) => void; onDragging: (value: boolean) => void
}) {
  const sentinelRef = useRef<HTMLDivElement>(null)
  const { folders: directoryFolders } = useLibraryDirectory()
  const folderNames = new Map(directoryFolders.map((folder) => [folder.id, folder.name]))
  const scope = all ? {} : folderId ? { folder_id: folderId } : { unclassified: true, include_folders: !!renderFolder }
  const { archives, folders = EMPTY_FOLDERS, entries, loading, error, refresh, indexing, hasMore, loadingMore, loadMore } = useArchivePage({ page: 1, page_size: 40, ...archiveQueryFilters(filters), ...scope }, "split")
  const { online, capabilities } = useAppAccess()
  const canMove = online && capabilities.archive_mutation
  const [moveKeys, setMoveKeys] = useState<string[] | null>(null)
  const [failedThumbs, setFailedThumbs] = useState<Set<string>>(() => new Set())
  const moved: OnLibraryMoved = (keys, destination) => {
    setMoveKeys((current) => current?.filter((key) => !keys.includes(key)) ?? null)
    onMoved(keys, destination)
  }
  const { busy, move } = useLibraryMove(moved)
  const { tasks } = useActiveTasks()
  const activeIds = tasks.map((task) => `${task.id}:${task.status}`).sort().join(",")
  const previousIds = useRef(activeIds)
  useEffect(() => { if (previousIds.current !== activeIds) { previousIds.current = activeIds; void refresh(true) } }, [activeIds, refresh])
  const processing = archives.some((archive) => archive.processing)
  useEffect(() => {
    if (!processing && !indexing) return
    const timer = window.setInterval(() => void refresh(true), 5000)
    return () => window.clearInterval(timer)
  }, [processing, indexing, refresh])
  const shown = archives
  const shownFolders = folders
  const shownEntries = entries
  useEffect(() => {
    const sentinel = sentinelRef.current
    if (!sentinel || !hasMore || loadingMore || error || typeof IntersectionObserver === "undefined") return
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) void loadMore()
    }, { root: sentinel.closest("[data-split-scroll]"), rootMargin: "200px" })
    observer.observe(sentinel)
    return () => observer.disconnect()
  }, [hasMore, loadingMore, error, loadMore])
  const archiveMap = new Map(shown.map((item) => [item.archive_id ?? item.path, item]))
  const folderMap = new Map(shownFolders.map((folder) => [folder.id, folder]))
  const visibleEntries = shownEntries ?? shown.map((item) => ({ kind: "archive" as const, id: item.archive_id ?? item.path }))
  useEffect(() => {
    const archive = archives.find((item) => (item.content_key || item.path) === active)
    if (!archive) return
    const index = entries?.findIndex((entry) => entry.kind === "archive" && entry.id === (archive.archive_id ?? archive.path))
      ?? archives.indexOf(archive)
    rememberLibraryArchive({ ...filters, scope: all ? "all" : "folders", folder: folderId }, archive, index)
  }, [active, archives, entries, filters, all, folderId])
  useEffect(() => { if (archives[0]) onFirst?.(archives[0]) }, [archives, onFirst])
  return <div className="space-y-1 py-1">
    {visibleEntries.map((entry) => {
      if (entry.kind === "folder") {
        const folder = folderMap.get(entry.id)
        return folder ? <div key={`folder:${folder.id}`}>{renderFolder?.(folder)}</div> : null
      }
      const archive = archiveMap.get(entry.id)
      if (!archive) return null
      const key = archive.content_key || archive.path
      return <div key={archive.path} className={cn("group flex min-w-0 items-center gap-1 rounded-md hover:bg-muted", active === key && "bg-muted ring-1 ring-border")}>
        {selectionMode && <input type="checkbox" checked={selected.has(key)} aria-label={`选择 ${archive.title}`} className="ml-1 size-3.5 shrink-0" onChange={() => onSelect(key)} />}
        <button data-content-key={key} aria-current={active === key ? "true" : undefined} className="flex min-w-0 flex-1 items-center gap-2 rounded-md p-2 text-left" onClick={() => {
          if (selectionMode) { onSelect(key); return }
          rememberLibraryArchive({ ...filters, scope: all ? "all" : "folders", folder: folderId }, archive, visibleEntries.indexOf(entry))
          onOpen(archive)
        }}
          draggable={canMove && !!archive.content_key && !busy} onDragStart={(event) => { startLibraryDrag(event, selected.has(key) ? [...selected] : [key]); onDragging(true) }} onDragEnd={() => onDragging(false)}>
          <span className="aspect-[535/304] w-16 shrink-0 overflow-hidden rounded bg-muted">
            {archive.has_thumbnail !== false && !failedThumbs.has(archive.path) ? (
              <img
                draggable={false}
                src={archive.thumbnail_url ?? api.archives.thumbnailUrl(archive.path)}
                alt=""
                loading="lazy"
                className="h-full w-full object-cover"
                onError={() => setFailedThumbs((prev) => new Set(prev).add(archive.path))}
              />
            ) : (
              <ArchiveCoverFallback
                title={archive.title}
                platform={resolveArchivePlatform(archive.metadata)}
                uploader={typeof archive.metadata.uploader === "string" ? archive.metadata.uploader : null}
                sourceUrl={typeof archive.metadata.source_url === "string" ? archive.metadata.source_url : null}
                mediaLabel={archive.has_video ? "视频" : archive.has_image ? "图文" : archive.metadata.content_subtype === "text_note" ? "正文" : "音频"}
                isVideo={archive.has_video}
                isImageNote={archive.has_image || archive.metadata.content_subtype === "image_note"}
                isTextNote={archive.metadata.content_subtype === "text_note"}
                variant="mini"
              />
            )}
          </span>
          <span className="min-w-0"><span className="line-clamp-2 text-xs leading-5">{archive.title}</span><span className="flex min-w-0 gap-1.5 text-[10px] text-muted-foreground"><span className="shrink-0">{archive.processing ? "处理中" : archive.metadata.status === "failed" ? "处理失败" : archive.date}</span>{all && archive.folder_id && folderNames.has(archive.folder_id) && <span className="flex min-w-0 items-center gap-0.5" title={`在文件夹「${folderNames.get(archive.folder_id)}」中`}><HugeiconsIcon icon={FolderOpenIcon} className="size-3 shrink-0" /><span className="truncate">{folderNames.get(archive.folder_id)}</span></span>}</span></span>
        </button>
        {canMove && archive.content_key && <DropdownMenu><DropdownMenuTrigger asChild><Button size="icon-sm" variant="ghost" className="mr-1 size-6 shrink-0 opacity-0 group-hover:opacity-100 focus-visible:opacity-100 max-md:opacity-100" aria-label={`${archive.title} 更多操作`}><HugeiconsIcon icon={MoreHorizontalIcon} className="size-3" /></Button></DropdownMenuTrigger><DropdownMenuContent align="start">
          <DropdownMenuItem onClick={() => onSelect(key)}>选择</DropdownMenuItem>
          <DropdownMenuItem onClick={() => setMoveKeys([key])}>移到文件夹…</DropdownMenuItem>
          {(folderId || (all && archive.folder_id)) && <DropdownMenuItem disabled={busy} onClick={() => void move([key], null)}>移到未分类</DropdownMenuItem>}
        </DropdownMenuContent></DropdownMenu>}
      </div>
    })}
    {loading && <p className="px-2 py-1 text-xs text-muted-foreground">正在加载…</p>}
    {error && <p role="alert" className="px-2 py-1 text-xs text-destructive">{error}</p>}
    {!loading && !visibleEntries.length && <p className="px-2 py-1 text-xs text-muted-foreground">没有匹配的内容</p>}
    {hasMore && <div ref={sentinelRef}><Button size="sm" variant="ghost" className="w-full text-xs" disabled={loadingMore} onClick={() => void loadMore()}>{loadingMore ? "正在加载…" : "加载更多"}</Button></div>}
    {moveKeys?.length ? <MoveContentsDialog contentKeys={moveKeys} sourceFolderId={folderId} onClose={() => setMoveKeys(null)} onMoved={moved} /> : null}
  </div>
}

function FilesSplitView({ filters, onOpen }: { filters: LibraryFilters; onOpen: (item: ArchiveItem) => void }) {
  const library = useLibraryControls()
  const scrollRef = useRef<HTMLDivElement>(null)
  const platform = usePlatform()
  const positionKey = `${archiveCacheScope()}:${platform.kind}:split:${JSON.stringify({ filters: archiveQueryFilters(filters), scope: library.scope })}`
  useListPosition(positionKey, scrollRef)
  const { online, capabilities } = useAppAccess()
  const [selectionMode, setSelectionMode] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(() => new Set())
  const [moveKeys, setMoveKeys] = useState<string[] | null>(null)
  const [dragging, setDragging] = useState(false)
  const filterKey = JSON.stringify(filters)
  const [selectionFilter, setSelectionFilter] = useState(filterKey)
  if (selectionFilter !== filterKey) {
    setSelectionFilter(filterKey); setSelectionMode(false); setSelected(new Set()); setMoveKeys(null); setDragging(false)
  }
  const onSelect = (key: string) => {
    setSelectionMode(true)
    setSelected((current) => { const next = new Set(current); if (next.has(key)) next.delete(key); else next.add(key); return next })
  }
  const onMoved: OnLibraryMoved = (keys, destination) => {
    setSelected((current) => new Set([...current].filter((key) => !keys.includes(key))))
    setMoveKeys((current) => current?.filter((key) => !keys.includes(key)) ?? null)
    if (library.item && keys.includes(library.item)) library.setLocation({ folder: destination || undefined }, false)
  }
  const { busy: moving, move } = useLibraryMove(onMoved)
  const groupProps = { filters, active: library.item, onMoved, selectionMode, selected, onSelect, onDragging: setDragging }
  const [expanded, setExpanded] = useState<Set<string>>(() => expandedFolders.get(positionKey) ?? new Set(library.folder ? [library.folder] : []))
  useEffect(() => {
    expandedFolders.set(positionKey, expanded)
    if (expandedFolders.size > 80) {
      const oldest = expandedFolders.keys().next().value
      if (oldest !== undefined) expandedFolders.delete(oldest)
    }
  }, [positionKey, expanded])
  const [lastFolder, setLastFolder] = useState(library.folder)
  const [autoSelect, setAutoSelect] = useState(true)
  const narrow = useMediaQuery("(max-width: 1099px)")
  if (lastFolder !== library.folder) {
    setLastFolder(library.folder)
    if (library.folder) setExpanded(new Set([...expanded, library.folder]))
  }
  useEffect(() => {
    if (narrow || selectionMode) return
    const browse = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.isComposing || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return
      if (event.target instanceof Element && event.target.closest("input, textarea, select, [contenteditable]:not([contenteditable=false]), [role=textbox]")) return
      if (document.querySelector('[role="dialog"], [role="alertdialog"], [role="menu"], [role="listbox"], [data-slot="dropdown-menu-content"][data-state="open"]')) return
      const direction = event.key === "ArrowDown" || event.key === "j" ? 1 : event.key === "ArrowUp" || event.key === "k" ? -1 : 0
      if (!direction) return
      const items = [...(scrollRef.current?.querySelectorAll<HTMLButtonElement>("button[data-content-key]") ?? [])]
        .filter((item) => !item.disabled && !item.closest("[hidden]"))
      if (!items.length) return
      const active = items.findIndex((item) => item.getAttribute("aria-current") === "true")
      const index = active < 0 ? (direction > 0 ? 0 : items.length - 1) : Math.max(0, Math.min(items.length - 1, active + direction))
      const next = items[index]
      event.preventDefault()
      next.click()
      next.scrollIntoView({ block: "nearest" })
    }
    window.addEventListener("keydown", browse)
    return () => window.removeEventListener("keydown", browse)
  }, [narrow, selectionMode])
  const { item: selectedItem, setLocation } = library
  const first = useCallback((item: ArchiveItem) => {
    if (!narrow && !selectedItem && autoSelect) {
      rememberLibraryArchive({ ...filters, scope: library.scope, folder: library.folder }, item, 0)
      setLocation({ item: item.content_key || item.path }, false)
    }
  }, [narrow, selectedItem, setLocation, autoSelect, filters, library.scope, library.folder])
  return <div className="relative flex h-full min-w-0">
      <aside aria-label="文件列表" hidden={narrow && !!library.item} className={cn("flex h-full shrink-0 flex-col overflow-hidden bg-background p-2", narrow ? "w-full" : "w-[21.333rem] border-r", narrow && !!library.item && "hidden")}>
        <div ref={scrollRef} data-split-scroll className="min-h-0 flex-1 overflow-y-auto">
        {library.scope === "all" ? <SplitViewGroup {...groupProps} all onOpen={onOpen} onFirst={first} /> : <SplitViewGroup {...groupProps} onOpen={onOpen} onFirst={!library.folder ? first : undefined} renderFolder={(folder) => <section className="mb-1" data-folder-id={folder.id}>
          <LibraryDropTarget label={folder.name} disabled={moving} onDropContents={(keys) => void move(keys, folder.id)}>
            <button type="button" aria-expanded={expanded.has(folder.id)} aria-controls={`folder-${folder.id}`} className="flex w-full min-w-0 items-center gap-2 rounded-md px-2 py-2 text-left text-sm hover:bg-muted" onClick={() => setExpanded((current) => { const next = new Set(current); if (next.has(folder.id)) next.delete(folder.id); else next.add(folder.id); return next })}>
              <HugeiconsIcon icon={expanded.has(folder.id) ? ArrowDown01Icon : ArrowRight01Icon} className="size-3 shrink-0" /><HugeiconsIcon icon={FolderOpenIcon} className="size-4 shrink-0 text-muted-foreground" /><span className="min-w-0 flex-1 truncate" title={folder.name}>{folder.name}</span><span className="text-xs text-muted-foreground">{folder.count}</span>
            </button>
          </LibraryDropTarget>
          {expanded.has(folder.id) && <div id={`folder-${folder.id}`} className="ml-4 border-l pl-1"><SplitViewGroup {...groupProps} folderId={folder.id} onOpen={onOpen} onFirst={library.folder === folder.id ? first : undefined} /><LibraryBatchActions scope={{ type: "folder", folder_id: folder.id }} compact onCompleted={(keys, action, destination) => {
            if (action === "move" && destination !== undefined) onMoved(keys, destination)
            if (action === "delete" && library.item && keys.includes(library.item)) { setAutoSelect(false); setLocation({ item: undefined }, false) }
          }} /></div>}
        </section>} />}
        </div>
        {online && capabilities.archive_mutation && <div className="shrink-0 space-y-2 border-t pt-2">
          {dragging && <LibraryDropTarget label="未分类" disabled={moving} onDropContents={(keys) => void move(keys, null)}><span className="block rounded-md border border-dashed px-2 py-3 text-center text-xs">拖到此处移到未分类</span></LibraryDropTarget>}
          {selectionMode ? <div className="flex flex-wrap items-center gap-1" role="toolbar" aria-label="选择内容">
            <span className="text-xs">已选 {selected.size} 项</span><Button size="sm" variant="ghost" className="ml-auto h-7 px-2 text-xs" onClick={() => { setSelectionMode(false); setSelected(new Set()) }}>取消</Button>
            <Button size="sm" variant="outline" className="h-7 px-2 text-xs" disabled={!selected.size || moving} onClick={() => setMoveKeys([...selected])}>移到文件夹…</Button>
            <Button size="sm" variant="outline" className="h-7 px-2 text-xs" disabled={!selected.size || moving} onClick={() => void move([...selected], null)}>移到未分类</Button>
          </div> : <Button size="sm" variant="ghost" className="h-7 w-full text-xs" onClick={() => setSelectionMode(true)}>选择内容</Button>}
        </div>}
      </aside>
    {(!narrow || library.item) && <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      {narrow && <div className="hidden shrink-0 border-b px-2 py-1 md:block"><Button size="sm" variant="ghost" className="h-11" onClick={library.returnToList}><HugeiconsIcon icon={ArrowLeft01Icon} className="size-4" />返回列表</Button></div>}
      <div className="min-h-0 flex-1"><Suspense fallback={<LoadingState title="正在打开内容" className="h-full" />}><SplitViewResult contentKey={library.item} /></Suspense></div>
    </div>}
    {moveKeys?.length ? <MoveContentsDialog contentKeys={moveKeys} onClose={() => setMoveKeys(null)} onMoved={onMoved} /> : null}
  </div>
}

function SplitViewResult({ contentKey }: { contentKey?: string }) {
  const route = useRoute()
  const platform = usePlatform()
  const narrow = useMediaQuery("(max-width: 1099px)")
  const [resolved, setResolved] = useState<{ key: string; archive: ArchiveItem | null; taskId: string | null; error?: string } | null>(null)
  useEffect(() => {
    if (!contentKey) return
    let cancelled = false
    const load = async () => {
      if (platform.isNative) {
        const repository = createArchiveRepository(platform)
        const items = await repository.list()
        const archive = items.find((item) => item.content_key === contentKey || item.path === contentKey) ?? null
        return { archive, task_id: archive?.task_id ?? null }
      }
      return api.library.resolve(contentKey)
    }
    void load().then((result) => { if (!cancelled) setResolved({ key: contentKey, archive: result.archive, taskId: result.task_id }) }).catch((error) => { if (!cancelled) setResolved({ key: contentKey, archive: null, taskId: null, error: String(error) }) })
    return () => { cancelled = true }
  }, [contentKey, platform, route.params.generation])
  useEffect(() => {
    if (resolved && resolved.key === contentKey && resolved.archive) {
      rememberLibraryArchive(libraryStateFromParams(route.params), resolved.archive)
    }
  }, [resolved, contentKey, route.params])
  const onTarget = useCallback((path: string | null, taskId?: string | null) => {
    if (!contentKey) return
    const current = parseHash(window.location.hash)
    if (current.page !== "files" || current.params.item !== contentKey || libraryStateFromParams(current.params).view !== "split") return
    if (current.params.attempt !== route.params.attempt || current.params.generation !== route.params.generation) return
    navigate(buildHash("files", { ...current.params, attempt: path || undefined, viewerTask: taskId || undefined, generation: Date.now() }), { replace: narrow })
  }, [contentKey, route.params.attempt, route.params.generation, narrow])
  if (!contentKey) return <EmptyState title="选择一个内容" description="从左侧文件列表选择视频或音频。" className="h-full" />
  if (!resolved || resolved.key !== contentKey) return <LoadingState title="正在打开内容" className="h-full" />
  const path = route.params.attempt ?? (route.params.viewerTask ? undefined : resolved.archive?.path)
  const taskId = route.params.attempt ? route.params.viewerTask ?? null : route.params.viewerTask ?? resolved.taskId
  if (path) return <ResultPageComplete key={`${contentKey}:${path}:${route.params.generation || ""}`} embedded archivePath={path} taskId={taskId} onTarget={onTarget} onDeleted={() => navigate(buildHash("files", { ...route.params, item: undefined, attempt: undefined, viewerTask: undefined, generation: undefined }), { replace: true })} />
  if (taskId) return <ResultPageLive key={taskId} taskId={taskId} embedded onTarget={onTarget} />
  return <EmptyState title="内容暂不可用" description={resolved.error ?? "该内容已被删除，或尚未生成归档。"} className="h-full" />
}
