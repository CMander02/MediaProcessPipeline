import { api } from "@/lib/api"
import { errorMessage } from "@/lib/notify"

/** The server accepts up to 100 contents in one move; selection can span more pages. */
export async function moveLibraryContents(contentKeys: string[], folderId: string | null) {
  const keys = [...new Set(contentKeys)]
  const done: string[] = [], failures: string[] = []
  for (let start = 0; start < keys.length; start += 100) {
    const batch = keys.slice(start, start + 100)
    try {
      const result = await api.library.move(batch, folderId)
      const outcomes = new Map(result.items.map((item) => [item.content_key, item]))
      for (const key of batch) {
        const item = outcomes.get(key)
        if (item?.outcome === "moved" || item?.outcome === "unchanged") done.push(key)
        else failures.push(item?.reason === "content_deleting" ? "内容正在删除" : item?.reason === "content_not_found" ? "内容已不存在" : item?.reason || "移动未完成")
      }
    } catch (error) {
      failures.push(`${batch.length} 项：${errorMessage(error)}`)
    }
  }
  return { done, failures }
}
