/**
 * Routes between live task view and completed archive viewer
 * based on the current URL route.
 */
import { useRoute } from "@/lib/router"
import { ResultPageLive } from "./result-page-live"
import { ResultPageComplete } from "./result-page-complete"

export function ResultPageWrapper() {
  const route = useRoute()

  if (route.resultType === "task" && route.resultId) {
    return <ResultPageLive taskId={route.resultId} />
  }

  if (route.resultType === "archive" && route.resultId) {
    return <ResultPageComplete archivePath={route.resultId} />
  }

  // Fallback — shouldn't happen with proper navigation
  return (
    <div className="flex h-full items-center justify-center text-muted-foreground">
      <p>无效的结果链接</p>
    </div>
  )
}
