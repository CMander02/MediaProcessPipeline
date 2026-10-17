import { useLibraryBatchActions, type OnBatchCompleted } from "@/hooks/use-library-batch-actions"
import type { BatchScope } from "@/lib/library-types"
import { Button } from "@/components/ui/button"
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"

export function LibraryBatchActions({ scope, onCompleted, compact = false }: {
  scope: BatchScope; onCompleted?: OnBatchCompleted; compact?: boolean
}) {
  const batch = useLibraryBatchActions(scope, onCompleted)
  return <>
    <DropdownMenu>
      <DropdownMenuTrigger asChild><Button size="sm" variant="outline" className={compact ? "h-7 px-2 text-xs" : ""} disabled={batch.disabled}>批量操作…</Button></DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        {batch.actions.map((action) => <DropdownMenuItem key={action.action} variant={action.destructive ? "destructive" : "default"} onClick={() => batch.open(action.action)}>{action.label}</DropdownMenuItem>)}
      </DropdownMenuContent>
    </DropdownMenu>
    {batch.dialogs}
  </>
}
