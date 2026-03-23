import { useEffect, useMemo, useRef, type RefCallback } from "react"
import { srtToVTT } from "@/lib/srt"

interface MediaPlayerProps {
  src: string
  type: "video" | "audio"
  bindMedia: RefCallback<HTMLMediaElement>
  /** SRT content for subtitle track (shown in fullscreen) */
  subtitleSrt?: string
}

export function MediaPlayer({ src, type, bindMedia, subtitleSrt }: MediaPlayerProps) {
  const mediaRef = useRef<HTMLMediaElement | null>(null)

  // Bind/unbind media element
  useEffect(() => {
    const el = mediaRef.current
    if (el) {
      const cleanup = bindMedia(el)
      return () => {
        if (typeof cleanup === "function") cleanup()
      }
    }
  }, [bindMedia, src])

  // Stop media loading on unmount to prevent lingering requests
  useEffect(() => {
    return () => {
      const el = mediaRef.current
      if (el) {
        el.pause()
        el.removeAttribute("src")
        el.load() // aborts any pending network requests
      }
    }
  }, [])

  // Create VTT blob URL from SRT content
  const vttUrl = useMemo(() => {
    if (!subtitleSrt) return null
    const vtt = srtToVTT(subtitleSrt)
    const blob = new Blob([vtt], { type: "text/vtt" })
    return URL.createObjectURL(blob)
  }, [subtitleSrt])

  // Clean up blob URL
  useEffect(() => {
    return () => {
      if (vttUrl) URL.revokeObjectURL(vttUrl)
    }
  }, [vttUrl])

  if (type === "video") {
    return (
      <div className="w-full rounded-lg overflow-hidden bg-black">
        <video
          ref={(el) => { mediaRef.current = el }}
          src={src}
          className="w-full object-contain"
          preload="metadata"
          controls
        >
          {vttUrl && (
            <track
              kind="subtitles"
              src={vttUrl}
              srcLang="zh"
              label="字幕"
              default
            />
          )}
        </video>
      </div>
    )
  }

  return (
    <div className="w-full rounded-lg overflow-hidden bg-muted p-6 flex items-center justify-center">
      <audio
        ref={(el) => { mediaRef.current = el }}
        src={src}
        className="w-full"
        preload="metadata"
        controls
      />
    </div>
  )
}
