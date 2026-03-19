import type { TaskStats } from "@/lib/api"

export function StatsBar({ stats }: { stats: TaskStats }) {
  return (
    <div className="flex items-center gap-4 text-sm tabular-nums">
      <Stat label="全部" value={stats.total} />
      <Stat label="完成" value={stats.completed ?? 0} color="text-emerald-600" />
      <Stat label="处理中" value={stats.processing ?? 0} color="text-blue-600" />
      <Stat label="排队" value={stats.queued ?? 0} color="text-amber-600" />
      <Stat label="失败" value={stats.failed ?? 0} color="text-red-600" />
    </div>
  )
}

function Stat({ label, value, color }: { label: string; value: number; color?: string }) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-muted-foreground">{label}</span>
      <span className={`font-semibold ${color ?? "text-foreground"}`}>{value}</span>
    </div>
  )
}
