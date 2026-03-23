/**
 * Routes between live task view and completed archive viewer
 * based on the current URL route.
 */
import { useEffect, useState } from "react"
import { useRoute } from "@/lib/router"
import { navigate } from "@/lib/router"
import { api } from "@/lib/api"
import { ResultPageLive } from "./result-page-live"
import { ResultPageComplete } from "./result-page-complete"
import { Loader2 } from "lucide-react"

export function ResultPageWrapper() {
  const route = useRoute()

  // Archive route (new default path) — pass taskId for SSE if available
  if (route.resultType === "archive" && route.resultId) {
    return (
      <ResultPageComplete
        archivePath={route.resultId}
        taskId={route.taskId}
      />
    )
  }

  // Legacy task route — resolve output_dir then redirect to archive view
  if (route.resultType === "task" && route.resultId) {
    return <TaskResolver taskId={route.resultId} />
  }

  // Fallback
  return (
    <div className="flex h-full items-center justify-center text-muted-foreground">
      <p>无效的结果链接</p>
    </div>
  )
}

/** Fetches task, redirects to archive view if output_dir exists, else shows legacy live view. */
function TaskResolver({ taskId }: { taskId: string }) {
  const [resolved, setResolved] = useState<"loading" | "live" | "archive">("loading")
  const [outputDir, setOutputDir] = useState<string | null>(null)

  useEffect(() => {
    api.tasks.get(taskId).then((task) => {
      const dir = task.result?.output_dir as string | undefined
      if (dir) {
        setOutputDir(dir)
        setResolved("archive")
      } else {
        setResolved("live")
      }
    }).catch(() => {
      setResolved("live")
    })
  }, [taskId])

  if (resolved === "loading") {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (resolved === "archive" && outputDir) {
    return <ResultPageComplete archivePath={outputDir} taskId={taskId} />
  }

  return <ResultPageLive taskId={taskId} />
}
