import { useState, useEffect } from "react"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api"

interface RenameDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  archivePath: string
  currentTitle: string
  onRenamed: (newTitle: string) => void
}

export function RenameDialog({
  open,
  onOpenChange,
  archivePath,
  currentTitle,
  onRenamed,
}: RenameDialogProps) {
  const [title, setTitle] = useState(currentTitle)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState("")

  useEffect(() => {
    if (open) {
      setTitle(currentTitle)
      setError("")
    }
  }, [open, currentTitle])

  const handleSave = async () => {
    const trimmed = title.trim()
    if (!trimmed) return
    setSaving(true)
    setError("")
    try {
      await api.archives.rename(archivePath, trimmed)
      onRenamed(trimmed)
      onOpenChange(false)
    } catch {
      setError("保存失败，请重试")
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>重命名</DialogTitle>
        </DialogHeader>
        <div className="py-2">
          <Input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleSave()
              if (e.key === "Escape") onOpenChange(false)
            }}
            autoFocus
            placeholder="输入新标题"
          />
          {error && <p className="mt-1.5 text-xs text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>
            取消
          </Button>
          <Button onClick={handleSave} disabled={!title.trim() || saving}>
            保存
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
