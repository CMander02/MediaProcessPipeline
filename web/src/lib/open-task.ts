import type { Task } from "@/lib/api"
import { navigate } from "@/lib/router"

/** Open a task where its output is: the archive once there is one, else the live task view. */
export function openTask(task: Pick<Task, "id" | "result">) {
  const outputDir = typeof task.result?.output_dir === "string" ? task.result.output_dir : ""
  navigate(outputDir
    ? `#/result/archive?path=${encodeURIComponent(outputDir)}&taskId=${encodeURIComponent(task.id)}`
    : `#/result/task/${task.id}`)
}
