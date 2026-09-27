/**
 * The "new processing" composer can be opened from anywhere (Ctrl+N, pasting a link,
 * dropping files, the clipboard prompt). Requests queue here until a composer takes them.
 * On the 处理 page its own composer takes them; elsewhere they open the dialog.
 */
import { useSyncExternalStore } from "react"

export interface ComposerIntake {
  id: number
  text?: string
  files?: File[]
}

interface ComposerState {
  open: boolean
  intake: ComposerIntake[]
}

let state: ComposerState = { open: false, intake: [] }
let nextId = 1
let pageComposers = 0
const listeners = new Set<() => void>()

function set(next: ComposerState) {
  state = next
  listeners.forEach((listener) => listener())
}

export function openComposer(payload: { text?: string; files?: File[] } = {}) {
  const intake = payload.text || payload.files?.length
    ? [...state.intake, { id: nextId++, ...payload }]
    : state.intake
  // The 处理 page already shows a composer; feed that one instead of stacking a dialog on it.
  set({ open: pageComposers > 0 ? state.open : true, intake })
}

export function closeComposer() {
  if (state.open) set({ ...state, open: false })
}

/** Hand queued text and files to the composer that is showing. */
export function takeComposerIntake(): ComposerIntake[] {
  if (state.intake.length === 0) return []
  const taken = state.intake
  set({ ...state, intake: [] })
  return taken
}

/** The 处理 page registers its composer while mounted. */
export function registerPageComposer(): () => void {
  pageComposers += 1
  if (state.open) set({ ...state, open: false })
  return () => {
    pageComposers -= 1
  }
}

export function isComposerShowing(): boolean {
  return state.open || pageComposers > 0
}

export function subscribeComposer(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function useComposerState(): ComposerState {
  return useSyncExternalStore(subscribeComposer, () => state)
}
