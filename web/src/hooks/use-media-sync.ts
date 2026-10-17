import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import type { Subtitle } from "@/lib/srt"
import { findSubtitleIndexAtTime } from "@/lib/srt"

interface UseMediaSyncOptions {
  subtitles: Subtitle[]
  /** Seek to this time (seconds) once media is ready */
  initialTime?: number
  /** Called on every timeupdate with the current time in seconds */
  onTimeUpdate?: (time: number) => void
}

export function useMediaSync({ subtitles, initialTime, onTimeUpdate }: UseMediaSyncOptions) {
  const mediaRef = useRef<HTMLMediaElement | null>(null)
  const [currentTime, setCurrentTime] = useState(0) // seconds
  const [duration, setDuration] = useState(0)
  const [isPlaying, setIsPlaying] = useState(false)
  const [autoScroll, setAutoScroll] = useState(true)
  const autoScrollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const initialTimeApplied = useRef(false)
  const currentTimeRef = useRef(0)

  const currentSegmentIndex = useMemo(() => {
    if (subtitles.length === 0) return -1
    const timeMs = currentTime * 1000
    const idx = findSubtitleIndexAtTime(subtitles, timeMs)
    return idx >= 0 && timeMs <= subtitles[idx].endTime ? idx : -1
  }, [currentTime, subtitles])

  // Stable refs for callbacks so bindMedia doesn't re-create on every render
  const onTimeUpdateCbRef = useRef(onTimeUpdate)
  const initialTimeRef = useRef(initialTime)

  useEffect(() => {
    onTimeUpdateCbRef.current = onTimeUpdate
    initialTimeRef.current = initialTime
  }, [initialTime, onTimeUpdate])

  // Bind media element events
  const bindMedia = useCallback((el: HTMLMediaElement | null) => {
    const previous = mediaRef.current
    const restoreTime = previous ? previous.currentTime : currentTimeRef.current || initialTimeRef.current
    if (previous && previous !== el) {
      previous.pause()
      onTimeUpdateCbRef.current?.(previous.currentTime)
    }
    mediaRef.current = el
    if (!el) return
    initialTimeApplied.current = false

    const handleTimeUpdate = () => {
      setCurrentTime(el.currentTime)
      currentTimeRef.current = el.currentTime
      onTimeUpdateCbRef.current?.(el.currentTime)
    }
    const handleDurationChange = () => {
      setDuration(el.duration || 0)
      // Apply initial time once media is ready
      if (!initialTimeApplied.current && restoreTime && restoreTime > 0 && el.duration > 0) {
        initialTimeApplied.current = true
        el.currentTime = Math.max(0, Math.min(restoreTime, el.duration - 0.1))
      }
    }
    const onPlay = () => setIsPlaying(true)
    const onPause = () => setIsPlaying(false)
    const onEnded = () => setIsPlaying(false)

    el.addEventListener("timeupdate", handleTimeUpdate)
    el.addEventListener("durationchange", handleDurationChange)
    el.addEventListener("play", onPlay)
    el.addEventListener("pause", onPause)
    el.addEventListener("ended", onEnded)

    // Initialize
    if (el.duration) {
      setDuration(el.duration)
      if (!initialTimeApplied.current && restoreTime && restoreTime > 0) {
        initialTimeApplied.current = true
        el.currentTime = Math.max(0, Math.min(restoreTime, el.duration - 0.1))
      }
    }

    return () => {
      currentTimeRef.current = el.currentTime
      onTimeUpdateCbRef.current?.(el.currentTime)
      el.pause()
      el.removeEventListener("timeupdate", handleTimeUpdate)
      el.removeEventListener("durationchange", handleDurationChange)
      el.removeEventListener("play", onPlay)
      el.removeEventListener("pause", onPause)
      el.removeEventListener("ended", onEnded)
    }
  }, [])

  const seekTo = useCallback((timeMs: number) => {
    if (mediaRef.current) {
      mediaRef.current.currentTime = timeMs / 1000
    }
    // Re-enable auto-scroll on explicit seek
    setAutoScroll(true)
  }, [])

  const togglePlay = useCallback(() => {
    if (!mediaRef.current) return
    if (mediaRef.current.paused) {
      mediaRef.current.play()
    } else {
      mediaRef.current.pause()
    }
  }, [])

  const setPlaybackRate = useCallback((rate: number) => {
    if (mediaRef.current) {
      mediaRef.current.playbackRate = rate
    }
  }, [])

  // Pause auto-scroll on manual transcript scroll, re-enable after 5s
  const onManualScroll = useCallback(() => {
    setAutoScroll(false)
    if (autoScrollTimerRef.current) {
      clearTimeout(autoScrollTimerRef.current)
    }
    autoScrollTimerRef.current = setTimeout(() => {
      setAutoScroll(true)
    }, 5000)
  }, [])

  // Cleanup timer
  useEffect(() => {
    return () => {
      if (autoScrollTimerRef.current) clearTimeout(autoScrollTimerRef.current)
    }
  }, [])

  return {
    mediaRef,
    bindMedia,
    currentTime,
    duration,
    isPlaying,
    currentSegmentIndex,
    autoScroll,
    seekTo,
    togglePlay,
    setPlaybackRate,
    onManualScroll,
  }
}
