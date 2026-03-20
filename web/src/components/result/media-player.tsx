import { useEffect, useRef, type RefCallback } from "react"

interface MediaPlayerProps {
  src: string
  type: "video" | "audio"
  bindMedia: RefCallback<HTMLMediaElement>
}

export function MediaPlayer({ src, type, bindMedia }: MediaPlayerProps) {
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

  if (type === "video") {
    return (
      <div className="w-full rounded-lg overflow-hidden bg-black">
        <video
          ref={(el) => { mediaRef.current = el }}
          src={src}
          className="w-full max-h-[300px] object-contain"
          preload="metadata"
          controls
        />
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
