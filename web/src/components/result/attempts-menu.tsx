import { useEffect, useState } from "react"

import { api, type LibraryMatch } from "@/lib/api"
import { navigate } from "@/lib/router"
import { cn } from "@/lib/utils"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"

function attemptStatus(match: LibraryMatch): { label: string; dot: string } {
  if (match.status === "paused") return { label: "已暂停", dot: "bg-amber-500" }
  if (match.processing) return { label: "处理中", dot: "bg-primary" }
  if (match.status === "failed") return { label: "失败", dot: "bg-destructive" }
  if (match.status === "cancelled") return { label: "已取消", dot: "bg-muted-foreground" }
  return { label: "已完成", dot: "bg-emerald-500" }
}

function shortTime(value: string | null | undefined): string {
  if (!value) return ""
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ""
  const pad = (part: number) => String(part).padStart(2, "0")
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

const samePath = (left: string, right: string) =>
  left.replace(/[\\/]+/g, "/").toLowerCase() === right.replace(/[\\/]+/g, "/").toLowerCase()

/** "×3" next to the title when the same link was processed several times; lists every run. */
export function AttemptsMenu({ source, currentPath }: { source: string | null | undefined; currentPath: string }) {
  const [attempts, setAttempts] = useState<{ source: string; matches: LibraryMatch[] } | null>(null)

  useEffect(() => {
    if (!source || !/^https?:\/\//i.test(source)) return
    let cancelled = false
    api.archives.lookup([source])
      .then(({ matches }) => { if (!cancelled) setAttempts({ source, matches: matches[source] ?? [] }) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [source, currentPath])

  const matches = attempts && attempts.source === source ? attempts.matches : []
  if (matches.length < 2) return null
  const sorted = [...matches].sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""))

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className="rounded bg-muted px-1.5 py-0.5 text-[11px] leading-none tabular-nums text-foreground/80 transition-colors hover:bg-muted/70"
          title={`这个来源处理过 ${matches.length} 次`}
        >
          ×{matches.length}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="min-w-64">
        <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">同一来源的 {matches.length} 次处理</DropdownMenuLabel>
        {sorted.map((match) => {
          const current = samePath(match.path, currentPath)
          const status = attemptStatus(match)
          return (
            <DropdownMenuItem
              key={match.path}
              disabled={current}
              onClick={() => navigate(`#/result/archive?path=${encodeURIComponent(match.path)}${match.task_id ? `&taskId=${encodeURIComponent(match.task_id)}` : ""}`)}
            >
              <span className={cn("size-2 shrink-0 rounded-full", status.dot)} />
              <span className="flex-1 tabular-nums">{status.label} · {shortTime(match.created_at)}</span>
              {current && <span className="text-xs text-muted-foreground">当前</span>}
            </DropdownMenuItem>
          )
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
