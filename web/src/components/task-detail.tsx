import { useEffect, useState } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { StepProgress } from "@/components/step-progress"
import { Progress } from "@/components/ui/progress"
import { STATUS_CONFIG } from "@/lib/constants"
import { api, subscribeTaskEvents, type Task } from "@/lib/api"
import { HugeiconsIcon } from "@hugeicons/react"
import { Cancel01Icon, CancelCircleIcon } from "@hugeicons/core-free-icons"
import { cn } from "@/lib/utils"

export function TaskDetail({ taskId, onClose }: { taskId: string; onClose: () => void }) {
  const [task, setTask] = useState<Task | null>(null)

  useEffect(() => {
    api.tasks.get(taskId).then(setTask).catch(() => {})
    const unsub = subscribeTaskEvents(taskId, () => {
      api.tasks.get(taskId).then(setTask).catch(() => {})
    })
    return unsub
  }, [taskId])

  if (!task) return null

  const cfg = STATUS_CONFIG[task.status] ?? STATUS_CONFIG.cancelled
  const pct = Math.round(task.progress * 100)
  const analysis = (task.result as Record<string, unknown>)?.analysis as Record<string, unknown> | undefined
  const outputDir = (task.result as Record<string, unknown>)?.output_dir as string | undefined

  const handleCancel = async () => {
    try {
      await api.tasks.cancel(taskId)
      api.tasks.get(taskId).then(setTask)
    } catch {}
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose} role="dialog" aria-label="Task detail">
      <div className="absolute inset-0 bg-black/20" />
      <Card
        className="relative w-full max-w-lg h-full rounded-none border-l shadow-xl overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <CardHeader className="flex-row items-center justify-between gap-2 space-y-0 pb-3">
          <CardTitle className="text-base truncate flex-1">{task.source.split(/[/\\]/).pop()}</CardTitle>
          <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close">
            <HugeiconsIcon icon={Cancel01Icon} className="h-4 w-4" />
          </Button>
        </CardHeader>

        <CardContent className="space-y-4">
          {/* Status */}
          <div className="flex items-center gap-2">
            <span className={cn("h-2.5 w-2.5 rounded-full", cfg.dot)} />
            <Badge variant="outline">{cfg.label}</Badge>
            <span className="text-sm font-mono text-muted-foreground ml-auto">{task.id.slice(0, 8)}</span>
          </div>

          {/* Progress */}
          {(task.status === "processing" || task.status === "queued") && (
            <>
              <Progress value={pct} className="h-2" />
              <StepProgress task={task} />
            </>
          )}

          <Separator />

          {/* Info */}
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
            <dt className="text-muted-foreground">来源</dt>
            <dd className="truncate font-mono text-xs" title={task.source}>{task.source}</dd>
            <dt className="text-muted-foreground">创建</dt>
            <dd>{new Date(task.created_at).toLocaleString()}</dd>
            {task.completed_at && (
              <>
                <dt className="text-muted-foreground">完成</dt>
                <dd>{new Date(task.completed_at).toLocaleString()}</dd>
              </>
            )}
            {outputDir && (
              <>
                <dt className="text-muted-foreground">输出</dt>
                <dd className="truncate font-mono text-xs" title={outputDir}>{outputDir}</dd>
              </>
            )}
          </dl>

          {/* Error */}
          {task.error && (
            <div className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
              {task.error}
            </div>
          )}

          {/* Analysis */}
          {analysis && (
            <>
              <Separator />
              <div className="space-y-2">
                <h3 className="text-sm font-semibold">内容分析</h3>
                <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
                  {analysis.language != null && (
                    <>
                      <dt className="text-muted-foreground">语言</dt>
                      <dd>{String(analysis.language)}</dd>
                    </>
                  )}
                  {analysis.content_type != null && (
                    <>
                      <dt className="text-muted-foreground">类型</dt>
                      <dd>{String(analysis.content_type)}</dd>
                    </>
                  )}
                  {Array.isArray(analysis.keywords) && analysis.keywords.length > 0 && (
                    <>
                      <dt className="text-muted-foreground">关键词</dt>
                      <dd className="flex flex-wrap gap-1">
                        {(analysis.keywords as string[]).map((k) => (
                          <Badge key={k} variant="secondary" className="text-xs">{k}</Badge>
                        ))}
                      </dd>
                    </>
                  )}
                </dl>
              </div>
            </>
          )}

          {/* Cancel button */}
          {(task.status === "queued" || task.status === "processing") && (
            <>
              <Separator />
              <Button variant="destructive" size="sm" onClick={handleCancel}>
                <HugeiconsIcon icon={CancelCircleIcon} className="h-4 w-4 mr-1.5" />
                取消任务
              </Button>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
