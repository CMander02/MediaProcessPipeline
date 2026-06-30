import { useCallback, useEffect, useRef, useState } from "react"
import { api } from "@/lib/api"

export interface ArchiveItem {
  title: string
  date: string
  path: string
  has_transcript: boolean
  has_summary: boolean
  has_mindmap: boolean
  has_video: boolean
  has_audio: boolean
  has_image: boolean
  media_file: string | null
  processing?: boolean
  task_id?: string
  metadata: Record<string, unknown>
  duration_seconds: number | null
  analysis: {
    language?: string
    content_type?: string
    main_topics?: string[]
    keywords?: string[]
    proper_nouns?: string[]
    speakers_detected?: number
    tone?: string
  }
}

export function useArchives() {
  const [archives, setArchives] = useState<ArchiveItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const loadedRef = useRef(false)

  const refresh = useCallback(async (silent = false) => {
    const showInitialLoader = !silent && !loadedRef.current
    try {
      if (showInitialLoader) setLoading(true)
      const data = await api.archives.list()
      setArchives((data.archives ?? []) as ArchiveItem[])
      setError(null)
    } catch (e) {
      setError(String(e))
    } finally {
      loadedRef.current = true
      if (showInitialLoader) setLoading(false)
    }
  }, [])

  const removeArchive = useCallback((path: string) => {
    setArchives((current) => current.filter((archive) => archive.path !== path))
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  return { archives, loading, error, refresh, removeArchive }
}
