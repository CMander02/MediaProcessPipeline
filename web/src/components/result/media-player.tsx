import { useEffect, useMemo, useRef, type RefCallback } from "react"
import Artplayer from "artplayer"
import { srtToVTT } from "@/lib/srt"

interface MediaPlayerProps {
  src: string
  type: "video" | "audio"
  bindMedia: RefCallback<HTMLMediaElement>
  /** SRT content for subtitle track */
  subtitleSrt?: string
  loop?: boolean
  onLoopChange?: (loop: boolean) => void
}

export function MediaPlayer({ src, type, bindMedia, subtitleSrt, loop = false, onLoopChange }: MediaPlayerProps) {
  if (type === "audio") {
    return <AudioPlayer src={src} bindMedia={bindMedia} loop={loop} />
  }
  return <VideoPlayer src={src} bindMedia={bindMedia} subtitleSrt={subtitleSrt} loop={loop} onLoopChange={onLoopChange} />
}

function VideoPlayer({
  src,
  bindMedia,
  subtitleSrt,
  loop,
  onLoopChange,
}: {
  src: string
  bindMedia: RefCallback<HTMLMediaElement>
  subtitleSrt?: string
  loop: boolean
  onLoopChange?: (loop: boolean) => void
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const artRef = useRef<Artplayer | null>(null)
  const cleanupRef = useRef<(() => void) | null>(null)

  // Create VTT blob URL from SRT content
  const vttUrl = useMemo(() => {
    if (!subtitleSrt) return null
    const vtt = srtToVTT(subtitleSrt)
    const blob = new Blob([vtt], { type: "text/vtt" })
    return URL.createObjectURL(blob)
  }, [subtitleSrt])

  useEffect(() => {
    return () => {
      if (vttUrl) URL.revokeObjectURL(vttUrl)
    }
  }, [vttUrl])

  useEffect(() => {
    if (!containerRef.current) return

    const options: Record<string, any> = {
      container: containerRef.current,
      url: src,
      volume: 1,
      autoSize: false,
      autoMini: false,
      loop,
      mutex: true,
      backdrop: true,
      fullscreen: true,
      // pip: true,  // 小窗 — 暂时关闭，未来可能加回
      setting: true,
      playbackRate: true,
      aspectRatio: false,
      screenshot: false,
      miniProgressBar: true,
      theme: "#3b82f6",
      lang: "zh-cn",
      moreVideoAttr: {
        crossOrigin: "anonymous",
        preload: "metadata",
        loop,
      },
      settings: [{
        html: "循环",
        tooltip: loop ? "开" : "关",
        switch: loop,
        onSwitch(item: any) {
          const nextState = !item.switch
          if (art.video) art.video.loop = nextState
          onLoopChange?.(nextState)
          item.tooltip = nextState ? "开" : "关"
          return nextState
        },
      }],
    }

    if (vttUrl) {
      options.subtitle = { url: vttUrl, type: "vtt", style: { "font-size": "18px" }, encoding: "utf-8" }
      options.settings = [...options.settings, {
        html: "字幕",
        tooltip: "开",
        switch: true,
        onSwitch(item: any) {
          const nextState = !item.switch
          art.subtitle.show = nextState
          item.tooltip = nextState ? "开" : "关"
          return nextState
        },
      }]
    }

    const art = new Artplayer(options as any)

    artRef.current = art

    // Expose the internal <video> element to useMediaSync
    art.on("ready", () => {
      const video = art.video
      if (video) {
        video.loop = loop
        const ret = bindMedia(video)
        if (typeof ret === "function") {
          cleanupRef.current = ret
        }
      }
    })

    return () => {
      if (cleanupRef.current) {
        cleanupRef.current()
        cleanupRef.current = null
      }
      bindMedia(null)
      if (artRef.current) {
        artRef.current.destroy(false)
        artRef.current = null
      }
    }
  }, [src, vttUrl, bindMedia, loop, onLoopChange])

  return (
    <div className="w-full rounded-lg overflow-hidden bg-black">
      <div ref={containerRef} className="w-full aspect-video" />
    </div>
  )
}

function AudioPlayer({ src, bindMedia, loop }: { src: string; bindMedia: RefCallback<HTMLMediaElement>; loop: boolean }) {
  const audioRef = useRef<HTMLAudioElement | null>(null)

  useEffect(() => {
    const el = audioRef.current
    if (el) {
      const cleanup = bindMedia(el)
      return () => {
        if (typeof cleanup === "function") cleanup()
      }
    }
  }, [bindMedia, src])

  useEffect(() => {
    return () => {
      const el = audioRef.current
      if (el) {
        el.pause()
        el.removeAttribute("src")
        el.load()
      }
    }
  }, [])

  return (
    <div className="w-full rounded-lg overflow-hidden bg-muted p-6 flex items-center justify-center">
      <audio
        ref={(el) => { audioRef.current = el }}
        src={src}
        className="w-full"
        preload="metadata"
        loop={loop}
        controls
      />
    </div>
  )
}
