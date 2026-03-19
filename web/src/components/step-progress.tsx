import { cn } from "@/lib/utils"
import { PIPELINE_STEPS } from "@/lib/constants"
import { Check, Loader2 } from "lucide-react"
import type { Task } from "@/lib/api"

export function StepProgress({ task }: { task: Task }) {
  return (
    <div className="flex items-center gap-1">
      {PIPELINE_STEPS.map((step, i) => {
        const isCompleted = task.completed_steps.includes(step.id)
        const isCurrent = task.current_step === step.id
        return (
          <div key={step.id} className="flex items-center gap-1">
            {i > 0 && (
              <div className={cn("h-px w-3", isCompleted ? "bg-emerald-400" : "bg-border")} />
            )}
            <div
              className={cn(
                "flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium transition-colors",
                isCompleted && "bg-emerald-50 text-emerald-700",
                isCurrent && "bg-blue-50 text-blue-700",
                !isCompleted && !isCurrent && "text-muted-foreground",
              )}
            >
              {isCompleted ? (
                <Check className="h-3 w-3" />
              ) : isCurrent ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <span className="h-3 w-3 inline-flex items-center justify-center">
                  <span className="h-1.5 w-1.5 rounded-full bg-current opacity-30" />
                </span>
              )}
              <span>{step.name}</span>
            </div>
          </div>
        )
      })}
    </div>
  )
}
