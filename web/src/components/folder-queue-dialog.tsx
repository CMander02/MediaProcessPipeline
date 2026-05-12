/**
 * Folder picker + batch queue dialog.
 * Browses filesystem via /api/filesystem/browse, then scans for media files,
 * and submits each as a task.
 */
import { useState, useEffect, useRef } from "react"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { api } from "@/lib/api"
import { HugeiconsIcon } from "@hugeicons/react"
import {
  ArrowRight01Icon,
  Folder01Icon,
  FolderOpenIcon,
  HardDriveIcon,
  Loading03Icon,
  PlayIcon,
  MusicNote01Icon,
  Video01Icon,
  Cancel01Icon,
} from "@hugeicons/core-free-icons"

interface BrowseItem {
  name: string
  path: string
  is_dir: boolean
  size: number | null
}

interface MediaFile {
  path: string
  name: string
  size: number
}

const MEDIA_EXTS = new Set([
  "mp4", "mkv", "avi", "webm", "mov", "flv", "wmv",
  "mp3", "wav", "aac", "flac", "ogg", "m4a", "wma",
])

function isVideo(name: string) {
  const ext = name.split(".").pop()?.toLowerCase() ?? ""
  return ["mp4", "mkv", "avi", "webm", "mov", "flv", "wmv"].includes(ext)
}

function formatSize(bytes: number) {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`
}

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  options: Record<string, unknown>
  onSubmitted?: () => void
}

export function FolderQueueDialog({ open, onOpenChange, options, onSubmitted }: Props) {
  const [currentPath, setCurrentPath] = useState("")
  const [items, setItems] = useState<BrowseItem[]>([])
  const [drives, setDrives] = useState<BrowseItem[]>([])
  const [loading, setLoading] = useState(false)
  const [selectedFolder, setSelectedFolder] = useState<string | null>(null)
  const [mediaFiles, setMediaFiles] = useState<MediaFile[]>([])
  const [scanning, setScanning] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [submitProgress, setSubmitProgress] = useState(0)
  const [failedFiles, setFailedFiles] = useState<string[]>([])
  const [done, setDone] = useState(false)
  const [pathInput, setPathInput] = useState("")
  const cancelledRef = useRef(false)

  // Load drives on open
  useEffect(() => {
    if (!open) return
    setSelectedFolder(null)
    setMediaFiles([])
    setDone(false)
    setSubmitProgress(0)
    setFailedFiles([])
    cancelledRef.current = false

    api.filesystem.drives()
      .then((data) => {
        if (data.success) setDrives((data.drives ?? []).map((d) => ({ ...d, size: d.size ?? null })))
      })
      .catch(() => {})

    // Start at home/user dir
    browse(".")
  }, [open])

  const browse = async (path: string) => {
    setLoading(true)
    setSelectedFolder(null)
    setMediaFiles([])
    try {
      const data = await api.filesystem.browse(path, "directory")
      if (data.success) {
        setCurrentPath(data.path)
        setPathInput(data.path)
        setItems(data.items ?? [])
      }
    } catch {
      // ignore
    } finally {
      setLoading(false)
    }
  }

  const selectFolder = async (path: string) => {
    setSelectedFolder(path)
    setScanning(true)
    setMediaFiles([])
    try {
      const data = await api.filesystem.scanFolder(path)
      if (data.success) setMediaFiles(data.files)
    } catch {
      setMediaFiles([])
    } finally {
      setScanning(false)
    }
  }

  const handleSubmitAll = async () => {
    if (!mediaFiles.length) return
    setSubmitting(true)
    setSubmitProgress(0)
    setFailedFiles([])
    cancelledRef.current = false
    let count = 0
    const failed: string[] = []
    for (const file of mediaFiles) {
      if (cancelledRef.current) break
      try {
        await api.tasks.create(file.path, options)
      } catch {
        failed.push(file.name)
      }
      count++
      setSubmitProgress(count)
    }
    setFailedFiles(failed)
    setSubmitting(false)
    setDone(true)
    if (!cancelledRef.current) onSubmitted?.()
  }

  const handleCancelSubmit = () => {
    cancelledRef.current = true
  }

  const handlePathSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (pathInput.trim()) browse(pathInput.trim())
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl max-h-[80vh] flex flex-col gap-0 p-0">
        <DialogHeader className="px-4 pt-4 pb-3 shrink-0">
          <DialogTitle>选择文件夹批量处理</DialogTitle>
        </DialogHeader>

        <div className="flex flex-col gap-3 px-4 flex-1 min-h-0 overflow-hidden">
          {/* Path bar */}
          <form onSubmit={handlePathSubmit} className="flex gap-2 shrink-0">
            <Input
              value={pathInput}
              onChange={(e) => setPathInput(e.target.value)}
              placeholder="输入文件夹路径..."
              className="flex-1 font-mono text-xs h-8"
            />
            <Button type="submit" size="sm" variant="outline" className="h-8">
              跳转
            </Button>
          </form>

          <div className="flex gap-3 flex-1 min-h-0 overflow-hidden">
            {/* Drive sidebar */}
            {drives.length > 0 && (
              <div className="w-24 shrink-0 flex flex-col gap-0.5 overflow-y-auto">
                <p className="text-[10px] text-muted-foreground uppercase tracking-wider px-1 mb-1">驱动器</p>
                {drives.map((d) => (
                  <button
                    key={d.path}
                    className="flex items-center gap-1.5 px-2 py-1.5 rounded text-xs hover:bg-muted transition-colors text-left"
                    onClick={() => browse(d.path)}
                  >
                    <HugeiconsIcon icon={HardDriveIcon} className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                    {d.name}
                  </button>
                ))}
              </div>
            )}

            {/* File browser */}
            <div className="flex-1 min-w-0 flex flex-col gap-2 overflow-hidden">
              <div className="flex-1 overflow-y-auto rounded border bg-muted/20">
                {loading ? (
                  <div className="flex items-center justify-center h-full py-8">
                    <HugeiconsIcon icon={Loading03Icon} className="h-5 w-5 animate-spin text-muted-foreground" />
                  </div>
                ) : (
                  <div className="divide-y divide-border/40">
                    {items.map((item) => (
                      <button
                        key={item.path}
                        className={`w-full flex items-center gap-2 px-3 py-2 text-sm text-left hover:bg-muted transition-colors ${
                          selectedFolder === item.path ? "bg-primary/10 text-primary" : ""
                        }`}
                        onClick={() => {
                          if (item.name === "..") {
                            browse(item.path)
                          } else {
                            selectFolder(item.path)
                          }
                        }}
                        onDoubleClick={() => {
                          if (item.is_dir) browse(item.path)
                        }}
                      >
                        {item.name === ".." ? (
                          <HugeiconsIcon icon={ArrowRight01Icon} className="h-4 w-4 text-muted-foreground rotate-180 shrink-0" />
                        ) : selectedFolder === item.path ? (
                          <HugeiconsIcon icon={FolderOpenIcon} className="h-4 w-4 text-primary shrink-0" />
                        ) : (
                          <HugeiconsIcon icon={Folder01Icon} className="h-4 w-4 text-muted-foreground shrink-0" />
                        )}
                        <span className="truncate">{item.name}</span>
                        <span
                          className="ml-auto shrink-0 hover:text-foreground cursor-pointer text-muted-foreground/50"
                          onClick={(e) => { e.stopPropagation(); browse(item.path) }}
                          title="进入文件夹"
                        >
                          <HugeiconsIcon icon={ArrowRight01Icon} className="h-3.5 w-3.5" />
                        </span>
                      </button>
                    ))}
                    {items.length === 0 && (
                      <p className="text-sm text-muted-foreground text-center py-8">文件夹为空</p>
                    )}
                  </div>
                )}
              </div>

              {/* Scan results */}
              {selectedFolder && (
                <div className="shrink-0 rounded border bg-muted/20 p-3">
                  <p className="text-xs text-muted-foreground mb-2 truncate">
                    已选: <span className="text-foreground font-mono">{selectedFolder}</span>
                  </p>
                  {scanning ? (
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                      <HugeiconsIcon icon={Loading03Icon} className="h-4 w-4 animate-spin" />
                      扫描中...
                    </div>
                  ) : done ? (
                    <div className="space-y-1">
                      <p className="text-sm text-emerald-600">
                        {cancelledRef.current
                          ? `已取消 — 已提交 ${submitProgress} / ${mediaFiles.length} 个文件`
                          : `✓ 已提交 ${mediaFiles.length} 个文件`
                        }
                      </p>
                      {failedFiles.length > 0 && (
                        <p className="text-xs text-destructive">
                          {failedFiles.length} 个失败: {failedFiles.join(", ")}
                        </p>
                      )}
                    </div>
                  ) : mediaFiles.length === 0 ? (
                    <p className="text-sm text-muted-foreground">未找到媒体文件</p>
                  ) : (
                    <div className="space-y-1 max-h-32 overflow-y-auto">
                      {mediaFiles.map((f) => (
                        <div key={f.path} className="flex items-center gap-2 text-xs">
                          {isVideo(f.name) ? (
                            <HugeiconsIcon icon={Video01Icon} className="h-3 w-3 shrink-0 text-muted-foreground" />
                          ) : (
                            <HugeiconsIcon icon={MusicNote01Icon} className="h-3 w-3 shrink-0 text-muted-foreground" />
                          )}
                          <span className="truncate flex-1">{f.name}</span>
                          <span className="text-muted-foreground shrink-0">{formatSize(f.size)}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>

        <DialogFooter className="px-4 py-3 shrink-0 border-t mt-3">
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={submitting}>
            {done ? "关闭" : "取消"}
          </Button>
          {submitting ? (
            <Button variant="destructive" onClick={handleCancelSubmit}>
              <HugeiconsIcon icon={Cancel01Icon} className="h-4 w-4 mr-1.5" />
              停止 ({submitProgress}/{mediaFiles.length})
            </Button>
          ) : (
            <Button
              onClick={handleSubmitAll}
              disabled={!selectedFolder || mediaFiles.length === 0 || done || scanning}
            >
              <HugeiconsIcon icon={PlayIcon} className="h-4 w-4 mr-1.5" />
              {mediaFiles.length > 0 ? `提交全部 ${mediaFiles.length} 个文件` : "提交"}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
