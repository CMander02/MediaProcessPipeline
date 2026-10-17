import { useMemo, useState } from "react"
import type { BilibiliCollectionResult, LibraryMatch } from "@/lib/api"
import { formatDuration } from "@/lib/format"
import { libraryStateOf } from "./source-meta"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"

interface CollectionSelectorProps {
  collection: BilibiliCollectionResult
  selectedIds: string[]
  library: Record<string, LibraryMatch[] | null>
  refreshing: boolean
  notice?: string
  onChange: (ids: string[]) => void
  onRefresh: () => void
  onBack: () => void
  onOpen: (match: LibraryMatch) => void
}

export function CollectionSelector({ collection, selectedIds, library, refreshing, notice, onChange, onRefresh, onBack, onOpen }: CollectionSelectorProps) {
  const [search, setSearch] = useState("")
  const [sort, setSort] = useState("original")
  const chosen = new Set(selectedIds)
  const rows = useMemo(() => {
    const query = search.trim().toLocaleLowerCase()
    const items = collection.items.map((item, index) => ({ ...item, position: item.position ?? index + 1 }))
      .filter((item) => !query || item.title.toLocaleLowerCase().includes(query))
    return items.sort((a, b) => {
      if (sort === "original") return a.position - b.position
      if (a.published_at == null && b.published_at != null) return 1
      if (a.published_at != null && b.published_at == null) return -1
      const difference = (a.published_at ?? 0) - (b.published_at ?? 0)
      return (sort === "newest" ? -difference : difference) || a.position - b.position
    })
  }, [collection.items, search, sort])

  return (
    <section className="flex h-[min(70dvh,42rem)] min-h-0 flex-col overflow-hidden rounded-xl border bg-background" aria-label="选择合集视频">
      <div className="shrink-0 space-y-3 border-b p-3 sm:p-4">
        <div className="flex items-start gap-3">
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-lg font-semibold">{collection.title || "选择视频"}</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              {collection.complete === false ? `已加载 ${collection.items.length}${collection.total != null ? `/共 ${collection.total}` : ""} 项，目录尚未完整` : `共 ${collection.items.length} 项`}
              {collection.collection_type === "multipart" && " · 默认按 P 顺序"}
            </p>
          </div>
          <Button variant="ghost" className="text-base" onClick={onBack}>返回提交</Button>
        </div>
        <div className="flex flex-wrap gap-2">
          <Input className="min-w-32 flex-1 text-base" placeholder="搜索视频标题" aria-label="搜索视频标题" value={search} onChange={(event) => setSearch(event.target.value)} />
          <select aria-label="排序" value={sort} onChange={(event) => setSort(event.target.value)} className="h-9 rounded-md border bg-background px-2 text-sm">
            <option value="original">{collection.collection_type === "multipart" ? "P 顺序" : "合集顺序"}</option>
            <option value="newest">发布时间：新到旧</option>
            <option value="oldest">发布时间：旧到新</option>
          </select>
          <Button variant="outline" className="text-base" disabled={refreshing} onClick={onRefresh}>{refreshing ? "刷新中…" : "刷新目录"}</Button>
        </div>
        {notice && <p role="status" className="text-sm text-muted-foreground">{notice}</p>}
      </div>
      <ul className="collection-scrollbar min-h-0 flex-1 divide-y overflow-y-scroll overscroll-contain" style={{ scrollbarGutter: "stable", scrollbarWidth: "auto" }} aria-label="合集视频">
        {rows.map((item) => {
          const matches = library[item.url]
          const state = libraryStateOf(matches ?? undefined)
          const status = matches == null ? "状态未确认" : !state ? "未导入" : state.kind === "completed" ? "已在库中" : state.kind === "processing" ? "处理中" : state.kind === "paused" ? "已暂停" : state.match.status === "cancelled" ? "已取消" : "之前失败"
          return (
            <li key={item.id} className="flex items-center gap-2 px-3 py-3 hover:bg-muted/40 sm:gap-3 sm:px-4">
              <label className="flex min-w-0 flex-1 cursor-pointer items-center gap-2 sm:gap-3">
                <input type="checkbox" className="size-4 shrink-0 accent-primary" checked={chosen.has(item.id)} aria-label={`选择 ${item.title}`} onChange={() => onChange(chosen.has(item.id) ? selectedIds.filter((id) => id !== item.id) : [...selectedIds, item.id])} />
                <span className="w-7 shrink-0 text-right text-sm tabular-nums text-muted-foreground">{item.position}</span>
                <span className="min-w-0 flex-1">
                  <span className="block text-base leading-snug [overflow-wrap:anywhere]">{item.title}</span>
                  <span className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-sm text-muted-foreground">
                    <span>{item.published_at == null ? "发布时间未知" : new Date(item.published_at * 1000).toLocaleDateString("zh-CN")}</span>
                    {item.duration != null && <span>{formatDuration(item.duration)}</span>}
                    <span>{status}</span>
                  </span>
                </span>
              </label>
              {state && <button type="button" className="shrink-0 text-sm text-primary hover:underline" onClick={() => onOpen(state.match)}>打开</button>}
            </li>
          )
        })}
        {!rows.length && <li className="px-4 py-8 text-center text-base text-muted-foreground">没有匹配的视频</li>}
      </ul>
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-t p-3 sm:px-4">
        <Button variant="outline" className="text-sm" onClick={() => onChange(collection.items.map((item) => item.id))}>{collection.complete === false ? "全选已加载" : "全选"}</Button>
        <Button variant="outline" className="text-sm" onClick={() => onChange([])}>清空</Button>
        <Button variant="outline" className="text-sm" onClick={() => onChange(collection.items.filter((item) => library[item.url]?.length === 0).map((item) => item.id))}>只选未导入</Button>
        <span className="ml-auto text-sm tabular-nums">已选 {selectedIds.length} 项</span>
      </div>
    </section>
  )
}
