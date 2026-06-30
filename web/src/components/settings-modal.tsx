import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { SettingsPanel } from "./settings-panel"

interface SettingsModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function SettingsModal({ open, onOpenChange }: SettingsModalProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[85vh] flex-col overflow-hidden sm:max-w-[1400px]">
        <DialogHeader className="shrink-0">
          <DialogTitle>设置</DialogTitle>
        </DialogHeader>
        <div className="min-h-0 flex-1">
          <SettingsPanel />
        </div>
      </DialogContent>
    </Dialog>
  )
}
