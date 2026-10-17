import { useState } from "react"
import { HugeiconsIcon } from "@hugeicons/react"
import { GridViewIcon, LayoutTwoColumnIcon, FolderAddIcon, Menu01Icon } from "@hugeicons/core-free-icons"
import { useLibraryControls, type LibraryScope } from "@/hooks/use-library-controls"
import { libraryChanged, useLibraryDirectory } from "@/hooks/use-library-directory"
import { useAppAccess } from "@/hooks/use-app-access-context"
import { usePreferences } from "@/hooks/use-preferences"
import { api } from "@/lib/api"
import { notifyError } from "@/lib/notify"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"

const SCOPES: Array<{ value: LibraryScope; label: string; hint: string }> = [
  { value: "all", label: "全部", hint: "所有内容按顺序排在一起，不分文件夹" },
  { value: "folders", label: "文件夹", hint: "文件夹和未归类的内容" },
]

/** Folders, or every item in one list. "文件夹" also returns to the folder overview from inside a folder. */
export function LibraryScopeSwitch({ className }: { className?: string }) {
  const library = useLibraryControls()
  const { update } = usePreferences()
  const choose = (scope: LibraryScope) => {
    update({ libraryScope: scope })
    if (scope !== library.scope) library.setScope(scope)
    else if (scope === "folders" && library.folder) library.setLocation({ folder: undefined, item: undefined })
  }
  return <div role="radiogroup" aria-label="文件范围" className={cn("shrink-0 items-center rounded-md bg-muted p-0.5", className)}>
    {SCOPES.map((scope) => {
      const active = library.scope === scope.value
      return <button key={scope.value} type="button" role="radio" aria-checked={active} title={scope.hint}
        className={cn("h-10 rounded px-3 text-sm font-medium transition-colors md:h-6 md:px-2 md:text-[13px]",
          active ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
          active && scope.value === "folders" && library.folder && "text-primary")}
        onClick={() => choose(scope.value)}>{scope.label}</button>
    })}
  </div>
}

/** Where in the library: the scope switch on desktop; on phones the switch sits in the search row. */
export function LibraryCrumbs() {
  const library = useLibraryControls()
  const { folders } = useLibraryDirectory()
  const folder = folders.find((item) => item.id === library.folder)
  const inFolder = library.scope === "folders" && library.folder
  return <nav aria-label="文件位置" className="flex min-w-0 items-center gap-1 text-sm">
    <LibraryScopeSwitch className="hidden md:flex" />
    {!inFolder && <span className="truncate text-base font-semibold md:hidden">{library.scope === "all" ? "全部" : "文件"}</span>}
    {inFolder && <>
      <button type="button" className="shrink-0 rounded px-1 py-1 text-base font-semibold text-muted-foreground md:hidden" onClick={() => library.setLocation({ folder: undefined, item: undefined })}>文件</button>
      <span className="text-muted-foreground">/</span>
      <span className="min-w-0 truncate px-1 font-medium max-md:text-base max-md:font-semibold" title={folder?.name}>{folder?.name ?? "文件夹"}</span>
    </>}
  </nav>
}

export function LibraryViewControls() {
  const library = useLibraryControls()
  const { online, capabilities } = useAppAccess()
  const [open, setOpen] = useState(false)
  const [name, setName] = useState("")
  const [busy, setBusy] = useState(false)
  const create = async () => {
    setBusy(true)
    try { await api.library.create(name); libraryChanged(); setOpen(false); setName("") }
    catch (error) { notifyError("创建文件夹失败", error) }
    finally { setBusy(false) }
  }
  return <div className="flex shrink-0 items-center gap-0.5">
    {library.view === "browse" && <Button className="min-[1100px]:hidden" size="icon-sm" variant="ghost" aria-label="显示文件列表" onClick={() => window.dispatchEvent(new Event("mpp:library-tree-toggle"))}><HugeiconsIcon icon={Menu01Icon} className="size-4" /></Button>}
    {capabilities.archive_mutation && <Button size="icon-sm" variant="ghost" disabled={!online} aria-label="新建文件夹" title="新建文件夹" onClick={() => setOpen(true)}><HugeiconsIcon icon={FolderAddIcon} className="size-4" /></Button>}
    {(["grid", "browse"] as const).map((view) => <Button key={view} size="icon-sm" variant={library.view === view ? "secondary" : "ghost"} aria-label={view === "grid" ? "网格视图" : "浏览视图"} title={view === "grid" ? "网格视图" : "浏览视图"} aria-pressed={library.view === view} onClick={() => library.setView(view)}><HugeiconsIcon icon={view === "grid" ? GridViewIcon : LayoutTwoColumnIcon} className="size-4" /></Button>)}
    <Dialog open={open} onOpenChange={setOpen}><DialogContent><DialogHeader><DialogTitle>新建文件夹</DialogTitle><DialogDescription>用文件夹整理内容。名称最多 120 个字符。</DialogDescription></DialogHeader><Input aria-label="文件夹名称" autoFocus maxLength={120} value={name} onChange={(event) => setName(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && name.trim()) void create() }} /><DialogFooter><Button variant="ghost" disabled={busy} onClick={() => setOpen(false)}>取消</Button><Button disabled={busy || !name.trim()} onClick={() => void create()}>创建</Button></DialogFooter></DialogContent></Dialog>
  </div>
}
