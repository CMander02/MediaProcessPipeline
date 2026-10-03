import { useEffect, useState } from "react"
import { HugeiconsIcon } from "@hugeicons/react"
import { ArrowLeft01Icon, Copy01Icon, Folder01Icon, HardDriveIcon, Loading03Icon } from "@hugeicons/core-free-icons"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { useAppAccess } from "@/hooks/use-app-access-context"
import { api } from "@/lib/api"
import { notifyError, notifySuccess } from "@/lib/notify"

interface FolderEntry {
  name: string
  path: string
}

function parentOf(path: string): string {
  const trimmed = path.replace(/[\\/]+$/, "")
  const cut = Math.max(trimmed.lastIndexOf("\\"), trimmed.lastIndexOf("/"))
  if (cut <= 0) return ""
  const parent = trimmed.slice(0, cut)
  // "D:" alone is the drive's current directory on Windows; keep the root.
  return /^[A-Za-z]:$/.test(parent) ? `${parent}\\` : parent
}

/** Folders below the drive; the backend refuses a drive root or its first level as the library. */
function folderDepth(path: string): number {
  return path.replace(/^[A-Za-z]:/, "").split(/[\\/]+/).filter(Boolean).length
}

/** Folders on the server: drives at the top, then one level at a time. */
function FolderBrowser({ path, onOpen }: { path: string; onOpen: (path: string) => void }) {
  const [entries, setEntries] = useState<FolderEntry[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const load = path
      ? api.filesystem.browse(path, "directory").then((result) => {
        if (!result.success) throw new Error(result.error || "打不开这个文件夹")
        // The listing includes "..", which the 上一级 button already covers.
        return result.items.filter((item) => item.is_dir && item.name !== "..")
      })
      : api.filesystem.drives().then((result) => result.drives)
    load
      .then((items) => {
        if (cancelled) return
        setEntries(items.map((item) => ({ name: item.name, path: item.path })))
        setError(null)
      })
      .catch((reason) => {
        if (cancelled) return
        setEntries([])
        setError(reason instanceof Error ? reason.message : String(reason))
      })
    return () => {
      cancelled = true
    }
  }, [path])

  return (
    <div className="rounded-md border">
      <div className="flex items-center gap-1 border-b px-1 py-1">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-7 gap-1 px-2 text-xs"
          disabled={!path}
          onClick={() => onOpen(parentOf(path))}
        >
          <HugeiconsIcon icon={ArrowLeft01Icon} className="size-3.5" />
          上一级
        </Button>
        <span className="min-w-0 truncate font-mono text-xs text-muted-foreground" title={path}>{path || "此电脑"}</span>
      </div>
      <ul className="max-h-56 overflow-y-auto py-1" aria-label="文件夹">
        {entries === null && (
          <li className="flex items-center gap-2 px-3 py-2 text-sm text-muted-foreground">
            <HugeiconsIcon icon={Loading03Icon} className="size-4 animate-spin" />
            正在读取…
          </li>
        )}
        {error && <li className="px-3 py-2 text-sm text-destructive">{error}</li>}
        {entries?.length === 0 && !error && <li className="px-3 py-2 text-sm text-muted-foreground">这里没有子文件夹</li>}
        {entries?.map((entry) => (
          <li key={entry.path}>
            <button
              type="button"
              onClick={() => onOpen(entry.path)}
              className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-muted"
            >
              <HugeiconsIcon icon={path ? Folder01Icon : HardDriveIcon} className="size-4 shrink-0 text-muted-foreground" />
              <span className="truncate">{entry.name || entry.path}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}

function CommandLine({ command }: { command: string }) {
  const copy = () => {
    void navigator.clipboard.writeText(command)
      .then(() => notifySuccess("已复制命令"))
      .catch((error) => notifyError("复制失败", error))
  }
  return (
    <div className="flex items-center gap-2 rounded-md bg-muted px-2 py-1.5">
      <code className="min-w-0 flex-1 truncate font-mono text-xs" title={command}>{command}</code>
      <Button type="button" variant="ghost" size="icon-sm" className="size-6" onClick={copy} aria-label="复制命令">
        <HugeiconsIcon icon={Copy01Icon} className="size-3.5" />
      </Button>
    </div>
  )
}

/**
 * Where the library lives. Switching only points MPP at another folder; moving the library there
 * is the storage migration, which needs MPP stopped and so runs from the command line.
 */
export function LibraryLocation({
  value,
  onSwitch,
}: {
  value: string
  /** Rejects with the backend's reason when the folder can't be used. */
  onSwitch: (path: string) => Promise<void>
}) {
  const { capabilities } = useAppAccess()
  const [open, setOpen] = useState(false)
  const [browsing, setBrowsing] = useState("")
  const [target, setTarget] = useState("")
  const [archiveCount, setArchiveCount] = useState<number | null>(null)
  const [switching, setSwitching] = useState(false)
  const [switchError, setSwitchError] = useState<string | null>(null)
  const canBrowse = capabilities?.filesystem_browse !== false

  useEffect(() => {
    let cancelled = false
    api.archives.page({ page: 1, page_size: 1, search: "", media: "all", source: "all", sort: "created_desc" })
      .then((page) => { if (!cancelled) setArchiveCount(page.total) })
      .catch(() => { if (!cancelled) setArchiveCount(null) })
    return () => {
      cancelled = true
    }
  }, [value])

  const begin = () => {
    setBrowsing(parentOf(value))
    setTarget("")
    setSwitchError(null)
    setOpen(true)
  }

  const chosen = target.trim()
  const unchanged = !chosen || chosen.replace(/[\\/]+$/, "").toLowerCase() === value.replace(/[\\/]+$/, "").toLowerCase()

  const tooShallow = Boolean(chosen) && folderDepth(chosen) < 2
  const ready = !unchanged && !tooShallow

  const switchNow = async () => {
    setSwitching(true)
    setSwitchError(null)
    try {
      await onSwitch(chosen)
      setOpen(false)
      notifySuccess("已切换资料库", chosen)
    } catch (error) {
      setSwitchError(error instanceof Error ? error.message : String(error))
    } finally {
      setSwitching(false)
    }
  }

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-3">
        <span className="w-24 shrink-0 text-sm text-muted-foreground">位置</span>
        <span className="min-w-0 flex-1 truncate font-mono text-sm" title={value}>{value || "未设置"}</span>
        <Button type="button" variant="outline" size="sm" onClick={begin}>
          更改…
        </Button>
      </div>
      {archiveCount !== null && (
        <p className="pl-[6.75rem] text-xs text-muted-foreground">{archiveCount} 个条目</p>
      )}

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>更改资料库位置</DialogTitle>
            <DialogDescription>
              切换后，MPP 读写新文件夹里的资料库。现在这个资料库的条目、任务记录和声纹都留在原处，不会移动也不会删除，切回来就能再看到。
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-3">
            {canBrowse && (
              <FolderBrowser
                path={browsing}
                onOpen={(path) => {
                  setBrowsing(path)
                  if (path) setTarget(path)
                }}
              />
            )}
            <div className="space-y-1.5">
              <label htmlFor="library-target" className="text-sm font-medium">新位置</label>
              <Input
                id="library-target"
                value={target}
                onChange={(event) => setTarget(event.target.value)}
                placeholder="例如 E:\Media\MPP"
                className="font-mono text-sm"
                autoComplete="off"
              />
              {tooShallow && (
                <p className="text-xs text-destructive">选一个至少两层深的文件夹，例如 E:\Media\MPP；不能直接用整个盘或盘下第一层。</p>
              )}
            </div>
            {ready && (
              <div className="space-y-1.5 rounded-md border px-3 py-2.5">
                <p className="text-sm font-medium">想把现在的资料库搬过去？</p>
                <p className="text-xs leading-5 text-muted-foreground">
                  先关掉 MPP，在项目目录运行第一条命令预览；确认没有冲突后运行第二条复制过去，原资料库保留不动。复制完成后再回到这里切换。
                </p>
                <CommandLine command={`mpp storage migrate --target "${chosen}"`} />
                <CommandLine command={`mpp storage migrate --target "${chosen}" --apply`} />
              </div>
            )}
            {switchError && <p role="alert" className="text-sm text-destructive">没能切换：{switchError}</p>}
          </div>

          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => setOpen(false)}>取消</Button>
            <Button type="button" onClick={() => void switchNow()} disabled={!ready || switching}>
              {switching && <HugeiconsIcon icon={Loading03Icon} className="size-4 animate-spin" />}
              切换到这里
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
