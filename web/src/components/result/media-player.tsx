import { useEffect, useRef, useState, type RefCallback } from "react"
import Artplayer from "artplayer"
import { HugeiconsIcon } from "@hugeicons/react"
import { Backward02Icon, Forward02Icon, MusicNote01Icon, PauseIcon, PlayIcon, RepeatIcon } from "@hugeicons/core-free-icons"
import { srtToVTT } from "@/lib/srt"
import { cn } from "@/lib/utils"
import { formatDuration } from "@/lib/format"

interface MediaPlayerProps {
  src: string
  type: "video" | "audio"
  bindMedia: RefCallback<HTMLMediaElement>
  /** SRT content for subtitle track */
  subtitleSrt?: string
  loop?: boolean
  onLoopChange?: (loop: boolean) => void
  mobile?: boolean
  poster?: string
  title?: string
  autoPlay?: boolean
}

export function MediaPlayer({ src, type, bindMedia, subtitleSrt, loop = false, onLoopChange, mobile = false, poster, title, autoPlay = false }: MediaPlayerProps) {
  if (type === "audio") {
    return <AudioPlayer src={src} bindMedia={bindMedia} loop={loop} onLoopChange={onLoopChange} mobile={mobile} poster={poster} title={title} autoPlay={autoPlay} />
  }
  return <VideoPlayer src={src} bindMedia={bindMedia} subtitleSrt={subtitleSrt} loop={loop} onLoopChange={onLoopChange} mobile={mobile} poster={poster} autoPlay={autoPlay} />
}

function VideoPlayer({
  src,
  bindMedia,
  subtitleSrt,
  loop,
  onLoopChange,
  mobile,
  poster,
  autoPlay,
}: {
  src: string
  bindMedia: RefCallback<HTMLMediaElement>
  subtitleSrt?: string
  loop: boolean
  onLoopChange?: (loop: boolean) => void
  mobile: boolean
  poster?: string
  autoPlay: boolean
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const artRef = useRef<Artplayer | null>(null)
  const cleanupRef = useRef<(() => void) | null>(null)
  const onLoopChangeRef = useRef(onLoopChange)
  const subtitleUpdateRef = useRef<Promise<void>>(Promise.resolve())
  const setupRef = useRef({ mobile, poster, autoPlay })

  useEffect(() => {
    onLoopChangeRef.current = onLoopChange
    setupRef.current = { mobile, poster, autoPlay }
  }, [onLoopChange, mobile, poster, autoPlay])

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
      fullscreenWeb: setupRef.current.mobile,
      autoOrientation: setupRef.current.mobile,
      playsInline: true,
      poster: setupRef.current.poster,
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
        if (setupRef.current.autoPlay) void art.play().catch(() => {})
      }
    })

    return () => {
      if (cleanupRef.current) {
        cleanupRef.current()
        cleanupRef.current = null
      }
      bindMedia(null)
      if (artRef.current) {
        if (artRef.current.fullscreenWeb) artRef.current.fullscreenWeb = false
        artRef.current.video.pause()
        artRef.current.video.removeAttribute("src")
        artRef.current.video.load()
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
    <div className={cn("w-full overflow-hidden bg-black", !mobile && "rounded-lg")}>
      <div ref={containerRef} className="w-full aspect-video" />
    </div>
  )
}

function AudioPlayer({ src, bindMedia, loop, onLoopChange, mobile, poster, title, autoPlay }: {
  src: string
  bindMedia: RefCallback<HTMLMediaElement>
  loop: boolean
  onLoopChange?: (loop: boolean) => void
  mobile: boolean
  poster?: string
  title?: string
  autoPlay: boolean
}) {
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const [playing, setPlaying] = useState(false)
  const [time, setTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [rate, setRate] = useState(1)
  const [error, setError] = useState<string | null>(null)

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
    if (autoPlay && audioRef.current) void audioRef.current.play().catch(() => {})
  }, [autoPlay, src])

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

  const seek = (next: number) => {
    const audio = audioRef.current
    if (!audio || !Number.isFinite(audio.duration)) return
    audio.currentTime = Math.max(0, Math.min(next, audio.duration))
    setTime(audio.currentTime)
  }
  const toggle = () => {
    const audio = audioRef.current
    if (!audio) return
    if (!audio.paused) audio.pause()
    else {
      setError(null)
      void audio.play().catch(() => setError("暂时无法播放，请再试一次。"))
    }
  }

  return (
    <div className={cn("w-full overflow-hidden", mobile ? "bg-zinc-950 px-4 pb-3 pt-14 text-white" : "flex items-center gap-3 rounded-lg bg-muted p-6")}>
      <audio
        ref={(el) => { audioRef.current = el }}
        src={src}
        className={mobile ? "hidden" : "min-w-0 flex-1"}
        preload="metadata"
        loop={loop}
        controls={!mobile}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => setPlaying(false)}
        onTimeUpdate={(event) => setTime(event.currentTarget.currentTime)}
        onDurationChange={(event) => setDuration(Number.isFinite(event.currentTarget.duration) ? event.currentTarget.duration : 0)}
        onRateChange={(event) => setRate(event.currentTarget.playbackRate)}
        onError={() => setError("音频暂不可用，请检查媒体文件。")}
      />
      {mobile && <>
        <div className="mb-3 flex min-w-0 items-center gap-3">
          <div className="flex size-16 shrink-0 items-center justify-center overflow-hidden rounded bg-white/10">{poster ? <img src={poster} alt="" className="size-full object-cover" /> : <HugeiconsIcon icon={MusicNote01Icon} className="size-7 text-white/65" />}</div>
          <div className="min-w-0"><p className="line-clamp-2 text-sm leading-6">{title || "音频播放"}</p><p className="mt-1 text-xs text-white/55">{playing ? "正在播放" : "音频"}</p></div>
        </div>
        <div className="flex items-center gap-2 text-[11px] tabular-nums text-white/65">
          <span>{formatDuration(time)}</span>
          <input aria-label="音频播放进度" type="range" min={0} max={duration || 1} step={0.1} value={Math.min(time, duration || 0)} disabled={!duration} onChange={(event) => seek(Number(event.target.value))} className="h-8 min-w-0 flex-1 accent-white" />
          <span>{formatDuration(duration)}</span>
        </div>
        <div className="flex items-center justify-between gap-2">
          <select aria-label="播放速度" value={rate} onChange={(event) => { const next = Number(event.target.value); setRate(next); if (audioRef.current) audioRef.current.playbackRate = next }} className="h-11 w-14 rounded bg-transparent text-xs text-white [&>option]:text-black">
            {[0.75, 1, 1.25, 1.5, 2].map((value) => <option key={value} value={value}>{value}×</option>)}
          </select>
          <button type="button" aria-label="快退15秒" onClick={() => seek(time - 15)} className="flex size-11 flex-col items-center justify-center"><HugeiconsIcon icon={Backward02Icon} className="size-5" /><span className="text-[10px]">15</span></button>
          <button type="button" aria-label={playing ? "暂停音频" : "播放音频"} onClick={toggle} className="flex size-14 shrink-0 items-center justify-center rounded-full bg-white text-zinc-950"><HugeiconsIcon icon={playing ? PauseIcon : PlayIcon} className="size-7" /></button>
          <button type="button" aria-label="快进15秒" onClick={() => seek(time + 15)} className="flex size-11 flex-col items-center justify-center"><HugeiconsIcon icon={Forward02Icon} className="size-5" /><span className="text-[10px]">15</span></button>
          <button type="button" aria-label={loop ? "关闭循环播放" : "开启循环播放"} aria-pressed={loop} onClick={() => onLoopChange?.(!loop)} className={cn("flex size-11 items-center justify-center rounded", loop ? "bg-white/15 text-white" : "text-white/50")}><HugeiconsIcon icon={RepeatIcon} className="size-5" /></button>
        </div>
        {error && <p role="alert" className="pt-2 text-xs text-red-300">{error}</p>}
      </>}
      {!mobile && onLoopChange && (
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
