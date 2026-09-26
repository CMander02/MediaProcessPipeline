import { toast } from "sonner"

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

/** Non-blocking error toast; replaces window.alert for failed actions. */
export function notifyError(title: string, error?: unknown, action?: { label: string; onClick: () => void }) {
  toast.error(title, {
    description: error === undefined ? undefined : errorMessage(error),
    action,
  })
}

export function notifySuccess(title: string, description?: string, action?: { label: string; onClick: () => void }) {
  toast.success(title, { description, action })
}
