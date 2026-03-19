import { useState } from "react"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Separator } from "@/components/ui/separator"
import { SubmitForm } from "@/components/submit-form"
import { StatsBar } from "@/components/stats-bar"
import { TaskCard } from "@/components/task-card"
import { TaskDetail } from "@/components/task-detail"
import { ResultViewer } from "@/components/result-viewer"
import { SettingsPanel } from "@/components/settings-panel"
import { EventLog } from "@/components/event-log"
import { useTasks } from "@/hooks/use-tasks"
import { AudioLines } from "lucide-react"

export default function App() {
  const { tasks, stats, refresh } = useTasks()
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null)

  const activeTasks = tasks.filter(
    (t) => t.status === "processing" || t.status === "queued",
  )
  const completedTasks = tasks.filter((t) => t.status === "completed")
  const recentCompleted = completedTasks.slice(0, 5)

  return (
    <div className="min-h-screen bg-background">
      {/* Header */}
      <header className="border-b bg-card">
        <div className="mx-auto max-w-6xl flex items-center gap-3 px-6 h-14">
          <AudioLines className="h-5 w-5 text-primary" aria-hidden="true" />
          <h1 className="text-base font-semibold">MediaProcessPipeline</h1>
          <span className="text-xs text-muted-foreground">
            音视频 &rarr; 结构化知识
          </span>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-6 py-6">
        <Tabs defaultValue="process" className="space-y-6">
          <TabsList>
            <TabsTrigger value="process">处理</TabsTrigger>
            <TabsTrigger value="history">历史</TabsTrigger>
            <TabsTrigger value="results">结果</TabsTrigger>
            <TabsTrigger value="settings">设置</TabsTrigger>
            <TabsTrigger value="logs">日志</TabsTrigger>
          </TabsList>

          {/* Process */}
          <TabsContent value="process" className="space-y-6">
            <StatsBar stats={stats} />
            <SubmitForm onSubmitted={refresh} />
            <Separator />

            {activeTasks.length > 0 && (
              <section className="space-y-3">
                <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider">
                  正在处理
                </h2>
                <div className="space-y-2">
                  {activeTasks.map((t) => (
                    <TaskCard key={t.id} task={t} onClick={() => setSelectedTaskId(t.id)} />
                  ))}
                </div>
              </section>
            )}

            {recentCompleted.length > 0 && (
              <section className="space-y-3">
                <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider">
                  最近完成
                </h2>
                <div className="space-y-2">
                  {recentCompleted.map((t) => (
                    <TaskCard key={t.id} task={t} onClick={() => setSelectedTaskId(t.id)} />
                  ))}
                </div>
              </section>
            )}

            {activeTasks.length === 0 && recentCompleted.length === 0 && (
              <div className="py-12 text-center text-muted-foreground">
                <AudioLines className="h-10 w-10 mx-auto mb-3 opacity-20" aria-hidden="true" />
                <p>粘贴链接或上传文件开始处理&hellip;</p>
              </div>
            )}
          </TabsContent>

          {/* History */}
          <TabsContent value="history" className="space-y-4">
            {tasks.length === 0 ? (
              <p className="text-sm text-muted-foreground py-8 text-center">暂无任务</p>
            ) : (
              <div className="grid gap-2">
                {tasks.map((t) => (
                  <TaskCard key={t.id} task={t} onClick={() => setSelectedTaskId(t.id)} />
                ))}
              </div>
            )}
          </TabsContent>

          {/* Results */}
          <TabsContent value="results">
            <ResultViewer tasks={completedTasks} />
          </TabsContent>

          {/* Settings */}
          <TabsContent value="settings">
            <SettingsPanel />
          </TabsContent>

          {/* Logs */}
          <TabsContent value="logs">
            <EventLog />
          </TabsContent>
        </Tabs>
      </main>

      {/* Task detail panel */}
      {selectedTaskId && (
        <TaskDetail
          taskId={selectedTaskId}
          onClose={() => setSelectedTaskId(null)}
        />
      )}
    </div>
  )
}
