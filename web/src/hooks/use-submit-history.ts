import { useCallback, useState } from "react"

const STORAGE_KEY = "mpp-submit-history"
const STORAGE_VERSION = 1
const MAX_HISTORY_ITEMS = 8

export interface SubmitHistoryItem {
  source: string
  submittedAt: string
}

interface StoredSubmitHistory {
  version: typeof STORAGE_VERSION
  items: SubmitHistoryItem[]
}

function browserStorage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : (window.localStorage ?? null)
  } catch {
    return null
  }
}

function readSubmitHistory(): SubmitHistoryItem[] {
  try {
    const raw = browserStorage()?.getItem(STORAGE_KEY)
    if (!raw) return []
    const stored = JSON.parse(raw) as Partial<StoredSubmitHistory>
    if (stored.version !== STORAGE_VERSION || !Array.isArray(stored.items)) return []
    return stored.items.filter((item) => (
      item
      && typeof item.source === "string"
      && typeof item.submittedAt === "string"
    )).slice(0, MAX_HISTORY_ITEMS)
  } catch {
    return []
  }
}

function writeSubmitHistory(items: SubmitHistoryItem[]) {
  const stored: StoredSubmitHistory = { version: STORAGE_VERSION, items }
  browserStorage()?.setItem(STORAGE_KEY, JSON.stringify(stored))
}

export function useSubmitHistory() {
  const [items, setItems] = useState<SubmitHistoryItem[]>(readSubmitHistory)

  const add = useCallback((source: string) => {
    if (!/^https?:\/\//i.test(source)) return
    setItems((current) => {
      const next = [
        { source, submittedAt: new Date().toISOString() },
        ...current.filter((item) => item.source !== source),
      ].slice(0, MAX_HISTORY_ITEMS)
      writeSubmitHistory(next)
      return next
    })
  }, [])

  const clear = useCallback(() => {
    browserStorage()?.removeItem(STORAGE_KEY)
    setItems([])
  }, [])

  return { items, add, clear }
}
