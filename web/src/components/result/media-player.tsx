import { useEffect, useRef, type RefCallback } from "react"
import Artplayer from "artplayer"
import { HugeiconsIcon } from "@hugeicons/react"
import { RepeatIcon } from "@hugeicons/core-free-icons"
import { srtToVTT } from "@/lib/srt"
import { cn } from "@/lib/utils"

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
    return <AudioPlayer src={src} bindMedia={bindMedia} loop={loop} onLoopChange={onLoopChange} />
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
  const onLoopChangeRef = useRef(onLoopChange)
  const subtitleUpdateRef = useRef<Promise<void>>(Promise.resolve())

  useEffect(() => {
    onLoopChangeRef.current = onLoopChange
  }, [onLoopChange])

  useEffect(() => {
    if (!containerRef.current) return

    const options: ConstructorParameters<typeof Artplayer>[0] = {
      container: containerRef.current,
      url: src,
      volume: 1,
      autoSize: false,
      autoMini: false,
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
        crossOrigin: "use-credentials",
        preload: "metadata",
      },
      settings: [{
        name: "loop",
        html: "循环",
        tooltip: "关",
        switch: false,
        onSwitch(item) {
          const nextState = !item.switch
          if (art.video) art.video.loop = nextState
          onLoopChangeRef.current?.(nextState)
          item.tooltip = nextState ? "开" : "关"
          return nextState
        },
      }],
    }

    const art = new Artplayer(options)

    artRef.current = art
    subtitleUpdateRef.current = Promise.resolve()

    // Expose the internal <video> element to useMediaSync
    art.on("ready", () => {
      const video = art.video
      if (video) {
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
  }, [src, bindMedia])

  useEffect(() => {
    const art = artRef.current
    if (!art) return
    art.video.loop = loop
    art.setting.update({ name: "loop", html: "循环", switch: loop, tooltip: loop ? "开" : "关" })
  }, [src, bindMedia, loop])

  useEffect(() => {
    const art = artRef.current
    if (!art) return
    const setting = art.setting.find("subtitle")
    if (subtitleSrt) {
      if (!setting) {
        art.subtitle.show = true
        art.setting.add({
          name: "subtitle",
          html: "字幕",
          tooltip: "开",
          switch: true,
          onSwitch(item) {
            const nextState = !item.switch
            art.subtitle.show = nextState
            item.tooltip = nextState ? "开" : "关"
            return nextState
          },
        })
      }
    } else {
      art.subtitle.show = false
      if (!setting) return
      art.setting.remove("subtitle")
    }

    // Replace only the subtitle track; keep the playing video and its current time.
    const vtt = subtitleSrt ? srtToVTT(subtitleSrt) : "WEBVTT\n\n"
    let cancelled = false
    // Artplayer does not cancel pending loads. Keep older loads from finishing last.
    subtitleUpdateRef.current = subtitleUpdateRef.current.then(async () => {
      if (cancelled) return
      const vttUrl = URL.createObjectURL(new Blob([vtt], { type: "text/vtt" }))
      try {
        await art.subtitle.switch(vttUrl, { type: "vtt", style: { fontSize: "18px" }, encoding: "utf-8" })
      } catch {
        // Artplayer already displays subtitle load errors in its notice area.
      } finally {
        URL.revokeObjectURL(vttUrl)
      }
    })
    return () => { cancelled = true }
  }, [src, bindMedia, subtitleSrt])

  return (
    <div className="w-full rounded-lg overflow-hidden bg-black">
      <div ref={containerRef} className="w-full aspect-video" />
    </div>
  )
}

function AudioPlayer({ src, bindMedia, loop, onLoopChange }: {
  src: string
  bindMedia: RefCallback<HTMLMediaElement>
  loop: boolean
  onLoopChange?: (loop: boolean) => void
}) {
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
    <div className="flex w-full items-center gap-3 overflow-hidden rounded-lg bg-muted p-6">
      <audio
        ref={(el) => { audioRef.current = el }}
        src={src}
        className="min-w-0 flex-1"
        preload="metadata"
        loop={loop}
        controls
      />
      {onLoopChange && (
        <button
          type="button"
          onClick={() => onLoopChange(!loop)}
          aria-pressed={loop}
          aria-label={loop ? "关闭循环播放" : "开启循环播放"}
          title={loop ? "循环播放：开" : "循环播放：关"}
          className={cn(
            "flex size-9 shrink-0 items-center justify-center rounded-md transition-colors",
            loop ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-background hover:text-foreground",
          )}
        >
          <HugeiconsIcon icon={RepeatIcon} className="size-4" />
        </button>
      )}
    </div>
  )
}
