import { useState } from "react"
import { useLibraryDirectory } from "@/hooks/use-library-directory"
import { useLibraryMove, type OnLibraryMoved } from "@/hooks/use-library-move"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"

export function MoveContentsDialog({ contentKeys, sourceFolderId, onClose, onMoved }: {
  contentKeys: string[]; sourceFolderId?: string; onClose: () => void; onMoved?: OnLibraryMoved
}) {
  const { folders } = useLibraryDirectory()
  const [destination, setDestination] = useState("")
  const { busy, move } = useLibraryMove(onMoved)
  return <Dialog open onOpenChange={(open) => { if (!open && !busy) onClose() }}>
    <DialogContent onInteractOutside={(event) => { if (busy) event.preventDefault() }} onEscapeKeyDown={(event) => { if (busy) event.preventDefault() }}>
      <DialogHeader><DialogTitle>移动 {contentKeys.length} 项内容</DialogTitle><DialogDescription>选择目标文件夹。内容的全部处理记录会一起归类。</DialogDescription></DialogHeader>
      <label className="space-y-2 text-sm">目标位置<select aria-label="目标位置" autoFocus className="block w-full rounded-md border bg-background p-2" value={destination} onChange={(event) => setDestination(event.target.value)}>
        <option value="" disabled>选择目标位置</option>
        {folders.filter((folder) => folder.id !== sourceFolderId).map((folder) => <option key={folder.id} value={folder.id}>{folder.name}</option>)}
        <option value="unclassified">未分类</option>
      </select></label>
      <DialogFooter><Button variant="ghost" disabled={busy} onClick={onClose}>取消</Button><Button disabled={busy || !destination} onClick={async () => {
        const done = await move(contentKeys, destination === "unclassified" ? null : destination)
        if (done.length === contentKeys.length) onClose()
      }}>{busy ? "正在移动…" : "移动"}</Button></DialogFooter>
    </DialogContent>
  </Dialog>
}
