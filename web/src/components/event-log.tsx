import { useEffect, useRef, useState } from "react"
import { ScrollArea } from "@/components/ui/scroll-area"
import { subscribeAllEvents } from "@/lib/api"
import { STEP_NAME } from "@/lib/constants"

interface LogEntry {
  ts: string
  tid: string
  type: string
  detail: string
}

export function EventLog() {
  const [entries, setEntries] = useState<LogEntry[]>([])
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const unsub = subscribeAllEvents((event) => {
      const ts = event.timestamp.split("T")[1]?.slice(0, 8) ?? event.timestamp
      const tid = event.task_id.slice(0, 8)
      let detail = ""
      const data = event.data
      if (data.step) {
        const stepName = STEP_NAME[data.step as string] ?? data.step
        const pct = data.progress ? ` ${Math.round(Number(data.progress) * 100)}%` : ""
        detail = `${stepName}${pct}`
      } else if (data.error) {
        detail = String(data.error).slice(0, 100)
      } else if (data.output_dir) {
        detail = `\u2192 ${data.output_dir}`
      }

      setEntries((prev) => {
        const next = [...prev, { ts, tid, type: event.type, detail }]
        return next.length > 200 ? next.slice(-200) : next
      })
    })
    return unsub
  }, [])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [entries])

  return (
    <div className="space-y-2">
      <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider">
        事件日志
      </h2>
      <ScrollArea className="h-[500px] rounded-md border bg-muted/30">
        <div className="p-3 font-mono text-xs space-y-0.5">
          {entries.length === 0 && (
            <p className="text-muted-foreground py-4 text-center font-sans text-sm">
              等待事件&hellip;
            </p>
          )}
          {entries.map((e, i) => (
            <div key={i} className="flex gap-2 leading-5">
              <span className="text-muted-foreground shrink-0">{e.ts}</span>
              <span className="text-blue-600 shrink-0">{e.tid}</span>
              <span className="text-amber-600 shrink-0 w-16">{e.type}</span>
              <span className="text-foreground truncate">{e.detail}</span>
            </div>
          ))}
          <div ref={bottomRef} />
        </div>
      </ScrollArea>
    </div>
  )
}
