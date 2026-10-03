import type { Task } from "@/lib/api"

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

/** Human-readable name for a task: its archive folder, else something recognizable from the source. */
export function taskDisplayTitle(task: Pick<Task, "source" | "result">): string {
  const outputDir = typeof task.result?.output_dir === "string" ? task.result.output_dir : ""
  const folder = outputDir.replace(/[\\/]+$/, "").split(/[\\/]/).pop()?.replace(/\s\(\d+\)$/, "")
  // Runs that failed before metadata arrived are archived under their UUID; the source says more.
  if (folder && !UUID_RE.test(folder)) return folder
  return sourceLabel(task.source)
}

export function sourceLabel(source: string): string {
  const bv = source.match(/\bBV[0-9A-Za-z]{10}\b/)
  if (bv) return `B站 ${bv[0]}`
  // Only web links: "C:\\videos\\a.mp4" also parses as a URL with scheme "c:".
  if (!/^https?:\/\//i.test(source)) return source.split(/[\\/]/).pop() || source
  try {
    const url = new URL(source)
    const host = url.hostname.replace(/^www\./, "")
    if (host.endsWith("youtube.com") && url.searchParams.get("v")) return `YouTube ${url.searchParams.get("v")}`
    if (host === "youtu.be") return `YouTube ${url.pathname.replace(/^\//, "")}`
    const tail = url.pathname.split("/").filter(Boolean).pop()
    return tail ? `${host} · ${decodeURIComponent(tail)}` : host
  } catch {
    return source.split(/[\\/]/).pop() || source
  }
}

/** Title for a card: runs that failed before metadata arrived are named after their UUID folder. */
export function archiveDisplayTitle(archive: { title: string; metadata?: Record<string, unknown> }): {
  title: string
  untitled: boolean
} {
  const title = (archive.title ?? "").trim()
  if (title && !UUID_RE.test(title)) return { title, untitled: false }
  const source = typeof archive.metadata?.source_url === "string" ? archive.metadata.source_url : ""
  return { title: source ? sourceLabel(source) : "未命名条目", untitled: true }
}

export interface CheckpointAction {
  label: string
  hint: string
}

/** What "rerun from checkpoint" means for a stopped task, or null when it doesn't apply. */
export function checkpointAction(status: string | null | undefined): CheckpointAction | null {
  if (status === "failed" || status === "cancelled") return { label: "从断点继续", hint: "已完成的步骤不重做" }
  if (status === "completed") return { label: "重新生成摘要和导图", hint: "保留现有字幕和说话人" }
  return null
}
