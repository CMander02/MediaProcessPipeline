import { useCallback, useEffect, useRef, useState } from "react"
import type { Subtitle } from "@/lib/srt"
import { findSubtitleIndexAtTime } from "@/lib/srt"

interface UseMediaSyncOptions {
  subtitles: Subtitle[]
}

export function useMediaSync({ subtitles }: UseMediaSyncOptions) {
  const mediaRef = useRef<HTMLMediaElement | null>(null)
  const [currentTime, setCurrentTime] = useState(0) // seconds
  const [duration, setDuration] = useState(0)
  const [isPlaying, setIsPlaying] = useState(false)
  const [currentSegmentIndex, setCurrentSegmentIndex] = useState(-1)
  const [autoScroll, setAutoScroll] = useState(true)
  const autoScrollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Update current segment on time change
  useEffect(() => {
    if (subtitles.length === 0) return
    const timeMs = currentTime * 1000
    const idx = findSubtitleIndexAtTime(subtitles, timeMs)
    // Only set if the subtitle actually contains currentTime
    if (idx >= 0 && timeMs <= subtitles[idx].endTime) {
      setCurrentSegmentIndex(idx)
    } else {
      setCurrentSegmentIndex(-1)
    }
  }, [currentTime, subtitles])

  // Bind media element events
  const bindMedia = useCallback((el: HTMLMediaElement | null) => {
    mediaRef.current = el
    if (!el) return

    const onTimeUpdate = () => setCurrentTime(el.currentTime)
    const onDurationChange = () => setDuration(el.duration || 0)
    const onPlay = () => setIsPlaying(true)
    const onPause = () => setIsPlaying(false)
    const onEnded = () => setIsPlaying(false)

    el.addEventListener("timeupdate", onTimeUpdate)
    el.addEventListener("durationchange", onDurationChange)
    el.addEventListener("play", onPlay)
    el.addEventListener("pause", onPause)
    el.addEventListener("ended", onEnded)

    // Initialize
    if (el.duration) setDuration(el.duration)

    return () => {
      el.removeEventListener("timeupdate", onTimeUpdate)
      el.removeEventListener("durationchange", onDurationChange)
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
