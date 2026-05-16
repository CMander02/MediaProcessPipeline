import { useCallback, useEffect, useState } from "react"
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

  const refresh = useCallback(async (silent = false) => {
    try {
      if (!silent) setLoading(true)
      const data = await api.archives.list()
      setArchives((data.archives ?? []) as ArchiveItem[])
      setError(null)
    } catch (e) {
      setError(String(e))
    } finally {
      if (!silent) setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  return { archives, loading, error, refresh }
}
