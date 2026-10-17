import { useState } from "react"
import { HugeiconsIcon } from "@hugeicons/react"
import { Delete01Icon, FolderOpenIcon, Image01Icon, MoreHorizontalIcon, PencilEdit01Icon } from "@hugeicons/core-free-icons"
import type { LibraryFolder } from "@/lib/library-types"
import { api } from "@/lib/api"
import { libraryChanged } from "@/hooks/use-library-directory"
import { notifyError } from "@/lib/notify"
import { useAppAccess } from "@/hooks/use-app-access-context"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuSub,
  DropdownMenuSubContent, DropdownMenuSubTrigger, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  ContextMenu, ContextMenuContent, ContextMenuItem, ContextMenuSeparator, ContextMenuSub,
  ContextMenuSubContent, ContextMenuSubTrigger, ContextMenuTrigger,
} from "@/components/ui/context-menu"
import { useLibraryBatchActions } from "@/hooks/use-library-batch-actions"

function shortDate(value?: string): string {
  const date = value ? new Date(value) : null
  if (!date || Number.isNaN(date.getTime())) return ""
  const sameYear = date.getFullYear() === new Date().getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, "0")
  const day = String(date.getDate()).padStart(2, "0")
  return sameYear ? `${month}-${day}` : `${date.getFullYear()}-${month}-${day}`
}

/**
 * A folder in the grid: the cover is the chosen content or the newest one, so new arrivals show
 * from outside. Sheets behind the cover set it apart from a single video.
 */
export function FolderCard({ folder, onOpen }: { folder: LibraryFolder; onOpen: () => void }) {
  const { capabilities, online } = useAppAccess()
  const [dialog, setDialog] = useState<"rename" | "delete" | null>(null)
  const [name, setName] = useState(folder.name)
  const [busy, setBusy] = useState(false)
  const [brokenCovers, setBrokenCovers] = useState<ReadonlySet<string>>(() => new Set())
  const [avatarBroken, setAvatarBroken] = useState(false)
  const batch = useLibraryBatchActions({ type: "folder", folder_id: folder.id })
  const editable = online && capabilities.archive_mutation
  const cover = (folder.previews ?? []).find((item) => !brokenCovers.has(item.content_key))
  const avatar = folder.collection && folder.collection.avatar !== "none" && !avatarBroken ? api.library.avatarUrl(folder.id) : null
  const updated = shortDate(folder.last_processed_at)
  const kind = folder.collection ? "合集" : "文件夹"

  const save = async () => {
    setBusy(true)
    try {
      if (dialog === "delete") await api.library.deleteFolder(folder.id)
      else await api.library.rename(folder.id, name)
      libraryChanged(); setDialog(null)
    } catch (error) { notifyError("文件夹操作失败", error) }
    finally { setBusy(false) }
  }
  const resetCover = async () => {
    try { await api.library.setCover(folder.id, null); libraryChanged() }
    catch (error) { notifyError("恢复自动封面失败", error) }
  }

  const menu = (kind: "dropdown" | "context") => {
    const Item = kind === "dropdown" ? DropdownMenuItem : ContextMenuItem
    const Separator = kind === "dropdown" ? DropdownMenuSeparator : ContextMenuSeparator
    const Sub = kind === "dropdown" ? DropdownMenuSub : ContextMenuSub
    const SubTrigger = kind === "dropdown" ? DropdownMenuSubTrigger : ContextMenuSubTrigger
    const SubContent = kind === "dropdown" ? DropdownMenuSubContent : ContextMenuSubContent
    return <>
      <Item onClick={onOpen}><HugeiconsIcon icon={FolderOpenIcon} className="size-4" />打开{kind === "dropdown" ? "文件夹" : ""}</Item>
      {editable && <Item onClick={() => { setName(folder.name); setDialog("rename") }}><HugeiconsIcon icon={PencilEdit01Icon} className="size-4" />重命名</Item>}
      {editable && folder.cover_content_key && <Item onClick={() => void resetCover()} title="封面重新跟随最新加入的内容"><HugeiconsIcon icon={Image01Icon} className="size-4" />恢复自动封面</Item>}
      {batch.actions.length > 0 && folder.count > 0 && <>
        <Separator />
        <Sub>
          <SubTrigger disabled={batch.disabled}>批量处理 {folder.count} 项</SubTrigger>
          <SubContent>
            {batch.actions.map((action) => <Item key={action.action} variant={action.destructive ? "destructive" : "default"} onClick={() => batch.open(action.action)}>{action.label}</Item>)}
          </SubContent>
        </Sub>
      </>}
      {editable && <><Separator /><Item variant="destructive" onClick={() => setDialog("delete")}><HugeiconsIcon icon={Delete01Icon} className="size-4" />删除文件夹…</Item></>}
    </>
  }

  return <article className="group min-w-0" data-folder-id={folder.id}>
    <ContextMenu>
      <ContextMenuTrigger asChild>
        <div className="relative">
          <button type="button" onClick={onOpen} aria-label={`打开${kind} ${folder.name}`}
            className="relative block aspect-[535/304] w-full rounded-md text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
            {/* Two sheets behind the cover; the whole box keeps the card ratio so grid rows stay aligned. */}
            <span aria-hidden="true" className="absolute inset-x-[7%] top-0 h-4 rounded-t-md bg-muted-foreground/20" />
            <span aria-hidden="true" className="absolute inset-x-[3.5%] top-[3px] h-4 rounded-t-md bg-muted-foreground/35" />
            <span className="absolute inset-x-0 bottom-0 top-[6px] overflow-hidden rounded-md bg-muted ring-1 ring-black/5 dark:ring-white/10">
              {cover ? (
                <img
                  key={cover.content_key}
                  draggable={false}
                  src={api.archives.thumbnailUrl(cover.path)}
                  alt=""
                  loading="lazy"
                  decoding="async"
                  onError={() => setBrokenCovers((current) => new Set([...current, cover.content_key]))}
                  className="size-full object-cover transition-transform duration-200 group-hover:scale-[1.03]"
                />
              ) : (
                <span className="flex size-full items-center justify-center">
                  <HugeiconsIcon icon={FolderOpenIcon} className="size-12 text-muted-foreground/60" strokeWidth={1.25} />
                </span>
              )}
              <span aria-hidden="true" className="absolute inset-x-0 bottom-0 h-2/5 bg-gradient-to-t from-black/45 to-transparent" />
              {avatar && <img src={avatar} alt="" draggable={false} onError={() => setAvatarBroken(true)}
                className="absolute bottom-1.5 left-1.5 size-7 rounded-full bg-muted object-cover ring-2 ring-white/90" />}
              <span className="absolute bottom-1.5 right-1.5 inline-flex items-center gap-1 rounded bg-black/70 px-1.5 py-0.5 text-[11px] font-medium text-white tabular-nums">
                <HugeiconsIcon icon={FolderOpenIcon} className="size-3" strokeWidth={2} />
                {folder.count}
              </span>
            </span>
          </button>
          <div className="absolute right-1.5 top-2.5 z-10 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100 max-md:opacity-100">
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button type="button" aria-label={`${folder.name} 更多操作`}
                  className="flex size-6 items-center justify-center rounded bg-background/95 shadow-sm hover:bg-background">
                  <HugeiconsIcon icon={MoreHorizontalIcon} className="size-4" />
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">{menu("dropdown")}</DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>
      </ContextMenuTrigger>
      <ContextMenuContent>{menu("context")}</ContextMenuContent>
    </ContextMenu>
    <div data-archive-info className="flex flex-col px-0.5 pt-[3px]">
      <button type="button" onClick={onOpen} className="min-h-[2lh] w-full text-left text-[13px] font-medium leading-[1.2] transition-colors group-hover:text-primary">
        <span className="line-clamp-2">{folder.name}</span>
      </button>
      <div className="flex h-3.5 min-w-0 items-center gap-1.5 overflow-hidden text-[11px] leading-none text-muted-foreground">
        <span className={cn("shrink-0 rounded px-1 py-px", folder.collection ? "bg-primary/10 text-primary" : "bg-muted text-foreground/75")}>{kind}</span>
        {folder.collection?.owner_name && folder.collection.owner_name !== folder.name && <span className="min-w-0 truncate" title={folder.collection.owner_name}>{folder.collection.owner_name}</span>}
        <span className="shrink-0 tabular-nums">{folder.count} 项</span>
        {updated && <span className="ml-auto shrink-0 tabular-nums" title="最近处理时间">{updated}</span>}
      </div>
    </div>
    {batch.dialogs}
    <Dialog open={dialog !== null} onOpenChange={(open) => { if (!open && !busy) setDialog(null) }}><DialogContent><DialogHeader><DialogTitle>{dialog === "delete" ? "删除文件夹" : "重命名文件夹"}</DialogTitle><DialogDescription>{dialog === "delete" ? `「${folder.name}」中的内容会移出文件夹。处理记录和文件会保留。` : "名称最多 120 个字符。"}</DialogDescription></DialogHeader>{dialog === "rename" && <Input autoFocus maxLength={120} value={name} onChange={(event) => setName(event.target.value)} aria-label="文件夹名称" onKeyDown={(event) => { if (event.key === "Enter" && name.trim()) void save() }} />}<DialogFooter><Button variant="ghost" disabled={busy} onClick={() => setDialog(null)}>取消</Button><Button variant={dialog === "delete" ? "destructive" : "default"} disabled={busy || (dialog === "rename" && !name.trim())} onClick={() => void save()}>确认</Button></DialogFooter></DialogContent></Dialog>
  </article>
}
