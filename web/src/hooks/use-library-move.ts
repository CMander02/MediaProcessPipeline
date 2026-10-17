import { useState } from "react"
import { libraryChanged, libraryDirectorySnapshot } from "@/hooks/use-library-directory"
import { moveLibraryContents } from "@/lib/library-move"
import { notifyError, notifySuccess } from "@/lib/notify"

export type OnLibraryMoved = (keys: string[], folderId: string | null) => void

export function useLibraryMove(onMoved?: OnLibraryMoved) {
  const [busy, setBusy] = useState(false)
  const move = async (keys: string[], folderId: string | null) => {
    if (busy || !keys.length) return []
    setBusy(true)
    try {
      const result = await moveLibraryContents(keys, folderId)
      if (result.done.length) {
        const name = folderId ? libraryDirectorySnapshot().folders.find((folder) => folder.id === folderId)?.name || "文件夹" : "未分类"
        notifySuccess(`${result.done.length} 项已移到「${name}」`)
        onMoved?.(result.done, folderId)
      }
      if (result.failures.length) notifyError("部分内容未能移动", result.failures.slice(0, 3).join("；"))
      libraryChanged()
      return result.done
    } finally { setBusy(false) }
  }
  return { busy, move }
}
