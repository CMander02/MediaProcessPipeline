import { useCallback, useEffect, useState } from "react"

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

  const refresh = useCallback(async () => {
    try {
      setLoading(true)
      const res = await fetch("/api/pipeline/archives")
      if (!res.ok) throw new Error(`${res.status}`)
      const data = await res.json()
      setArchives(data.archives ?? [])
      setError(null)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  return { archives, loading, error, refresh }
}
