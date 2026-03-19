import { useState } from "react"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { ScrollArea } from "@/components/ui/scroll-area"
import type { Task } from "@/lib/api"

export function ResultViewer({ tasks }: { tasks: Task[] }) {
  const [selectedId, setSelectedId] = useState<string>("")
  const [content, setContent] = useState<{ summary: string; polished: string; srt: string } | null>(null)
  const [loading, setLoading] = useState(false)

  const loadResult = async (taskId: string) => {
    setSelectedId(taskId)
    setLoading(true)
    try {
      // Find the task to get output_dir from result
      const task = tasks.find((t) => t.id === taskId)
      const outputDir = (task?.result as Record<string, unknown>)?.output_dir as string | undefined
      if (!outputDir) {
        setContent({ summary: "No output directory", polished: "", srt: "" })
        return
      }

      // Fetch files via API
      const [summary, polished, srt] = await Promise.all([
        fetchFile(outputDir, "summary.md"),
        fetchFile(outputDir, "transcript_polished.md"),
        fetchFile(outputDir, "transcript.srt"),
      ])
      setContent({ summary, polished, srt })
    } catch {
      setContent({ summary: "Failed to load results", polished: "", srt: "" })
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-4">
      <Select value={selectedId} onValueChange={(v) => v && loadResult(v)}>
        <SelectTrigger className="w-full">
          <SelectValue placeholder="选择已完成的任务查看结果&hellip;" />
        </SelectTrigger>
        <SelectContent>
          {tasks.length === 0 ? (
            <SelectItem value="_" disabled>暂无已完成任务</SelectItem>
          ) : (
            tasks.map((t) => {
              const name = t.source.replace(/\\/g, "/").split("/").pop() ?? t.source
              return (
                <SelectItem key={t.id} value={t.id}>
                  <span className="font-mono text-xs mr-2">{t.id.slice(0, 8)}</span>
                  <span className="truncate">{name.length > 50 ? `\u2026${name.slice(-47)}` : name}</span>
                </SelectItem>
              )
            })
          )}
        </SelectContent>
      </Select>

      {loading && <p className="text-sm text-muted-foreground">Loading&hellip;</p>}

      {content && !loading && (
        <Tabs defaultValue="summary">
          <TabsList>
            <TabsTrigger value="summary">摘要</TabsTrigger>
            <TabsTrigger value="polished">润色字幕</TabsTrigger>
            <TabsTrigger value="srt">原始 SRT</TabsTrigger>
          </TabsList>
          <TabsContent value="summary">
            <ScrollArea className="h-[600px] rounded-md border p-4">
              <pre className="whitespace-pre-wrap text-sm leading-relaxed font-sans">
                {content.summary || "无内容"}
              </pre>
            </ScrollArea>
          </TabsContent>
          <TabsContent value="polished">
            <ScrollArea className="h-[600px] rounded-md border p-4">
              <pre className="whitespace-pre-wrap text-sm leading-relaxed font-sans">
                {content.polished || "无内容"}
              </pre>
            </ScrollArea>
          </TabsContent>
          <TabsContent value="srt">
            <ScrollArea className="h-[600px] rounded-md border p-4">
              <pre className="whitespace-pre-wrap text-sm leading-relaxed font-mono text-xs">
                {content.srt || "无内容"}
              </pre>
            </ScrollArea>
          </TabsContent>
        </Tabs>
      )}
    </div>
  )
}

async function fetchFile(outputDir: string, filename: string): Promise<string> {
  try {
    const res = await fetch(`/api/filesystem/read?path=${encodeURIComponent(outputDir + "/" + filename)}`)
    if (!res.ok) return ""
    const data = await res.json()
    return data.content ?? ""
  } catch {
    return ""
  }
}
