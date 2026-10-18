/** @vitest-environment jsdom */

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { MediaPlayer } from "./media-player"

interface MockSetting {
  name: string
  html: string
  tooltip?: string
  switch?: boolean
  onSwitch?: (item: MockSetting) => boolean
}

const mocks = vi.hoisted(() => {
  class Player {
    static instances: Player[] = []
    video = document.createElement("video")
    settings = new Map<string, MockSetting>()
    fullscreenWeb = false
    destroy = vi.fn(() => this.video.remove())
    subtitle = { show: true, switch: vi.fn<(url: string, options?: unknown) => Promise<string>>(async () => "blob:loaded") }
    setting = {
      find: (name: string) => this.settings.get(name),
      add: (item: MockSetting) => this.settings.set(item.name, item),
      update: (item: MockSetting) => this.settings.set(item.name, { ...this.settings.get(item.name), ...item }),
      remove: (name: string) => this.settings.delete(name),
    }

    constructor(options: { container: HTMLDivElement; url: string; settings: MockSetting[] }) {
      Player.instances.push(this)
      this.video.src = options.url
      Object.defineProperty(this.video, "paused", { configurable: true, writable: true, value: true })
      this.video.play = vi.fn(async () => { Object.defineProperty(this.video, "paused", { value: false }) })
      this.video.pause = vi.fn(() => { Object.defineProperty(this.video, "paused", { value: true }) })
      this.video.load = vi.fn(() => { this.video.currentTime = 0 })
      options.container.append(this.video)
      options.settings.forEach((item) => this.setting.add(item))
    }

    on(event: string, callback: () => void) {
      if (event === "ready") callback()
    }
  }

  return { Player, createObjectURL: vi.fn(), revokeObjectURL: vi.fn() }
})

vi.mock("artplayer", () => ({ default: mocks.Player }))

const originalSrt = "1\n00:00:10,000 --> 00:00:15,000\n[说话人 1] 原字幕\n"

function readBlob(blob: Blob) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = reject
    reader.readAsText(blob)
  })
}

beforeEach(() => {
  mocks.Player.instances = []
  mocks.createObjectURL.mockReset().mockImplementation(() => `blob:subtitle-${mocks.createObjectURL.mock.calls.length}`)
  mocks.revokeObjectURL.mockReset()
  vi.stubGlobal("URL", Object.assign(class extends URL {}, {
    createObjectURL: mocks.createObjectURL,
    revokeObjectURL: mocks.revokeObjectURL,
  }))
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe("video playback while editing", () => {
  it.each([
    ["subtitle text", originalSrt.replace("原字幕", "修改后的字幕")],
    ["speaker name", originalSrt.replace("说话人 1", "洪乐潼")],
  ])("keeps the playing video when saving %s", async (_label, updatedSrt) => {
    const bindMedia = vi.fn()
    const view = render(<MediaPlayer src="/video.mp4" type="video" bindMedia={bindMedia} subtitleSrt={originalSrt} />)
    await act(async () => {})
    const player = mocks.Player.instances[0]
    player.video.currentTime = 12.5
    player.video.playbackRate = 1.5
    player.video.volume = 0.4
    await player.video.play()

    view.rerender(<MediaPlayer src="/video.mp4" type="video" bindMedia={bindMedia} subtitleSrt={updatedSrt} />)
    await act(async () => {})

    expect(mocks.Player.instances).toHaveLength(1)
    expect(view.container.querySelector("video")).toBe(player.video)
    expect(player.video.paused).toBe(false)
    expect(player.video.currentTime).toBe(12.5)
    expect(player.video.playbackRate).toBe(1.5)
    expect(player.video.volume).toBe(0.4)
    expect(player.video.pause).not.toHaveBeenCalled()
    expect(player.video.load).not.toHaveBeenCalled()
    expect(player.destroy).not.toHaveBeenCalled()
    expect(bindMedia).toHaveBeenCalledTimes(1)
    expect(player.subtitle.switch).toHaveBeenCalledTimes(2)
    const vtt = await readBlob(mocks.createObjectURL.mock.calls[1][0])
    expect(vtt).toContain(updatedSrt.split("\n")[2].replace(/^\[[^\]]+\]\s*/, ""))
    expect(vtt).toContain("00:00:10.000 -->")
    expect(mocks.revokeObjectURL).toHaveBeenCalledWith("blob:subtitle-1")
    expect(mocks.revokeObjectURL).toHaveBeenCalledWith("blob:subtitle-2")
  })

  it("updates loop and uses the current callback without rebuilding the video", async () => {
    const bindMedia = vi.fn()
    const oldCallback = vi.fn()
    const newCallback = vi.fn()
    const view = render(<MediaPlayer src="/video.mp4" type="video" bindMedia={bindMedia} onLoopChange={oldCallback} />)
    const player = mocks.Player.instances[0]
    player.video.currentTime = 12.5
    await player.video.play()

    view.rerender(<MediaPlayer src="/video.mp4" type="video" bindMedia={bindMedia} loop onLoopChange={newCallback} />)

    expect(mocks.Player.instances).toHaveLength(1)
    expect(player.video.loop).toBe(true)
    expect(player.video.paused).toBe(false)
    expect(player.video.currentTime).toBe(12.5)
    const setting = player.setting.find("loop")!
    expect(setting.switch).toBe(true)
    setting.onSwitch?.(setting)
    expect(oldCallback).not.toHaveBeenCalled()
    expect(newCallback).toHaveBeenCalledWith(false)
    expect(player.video.loop).toBe(false)
  })

  it("adds, updates, and clears subtitles without resetting playback or the subtitle visibility choice", async () => {
    const bindMedia = vi.fn()
    const view = render(<MediaPlayer src="/video.mp4" type="video" bindMedia={bindMedia} />)
    const player = mocks.Player.instances[0]
    player.video.currentTime = 12.5
    await player.video.play()

    view.rerender(<MediaPlayer src="/video.mp4" type="video" bindMedia={bindMedia} subtitleSrt={originalSrt} />)
    await act(async () => {})
    expect(player.subtitle.show).toBe(true)
    const setting = player.setting.find("subtitle")!
    setting.switch = setting.onSwitch?.(setting)
    expect(player.subtitle.show).toBe(false)
    view.rerender(<MediaPlayer src="/video.mp4" type="video" bindMedia={bindMedia} subtitleSrt={originalSrt.replace("原字幕", "新字幕")} />)
    await act(async () => {})
    expect(player.subtitle.show).toBe(false)
    view.rerender(<MediaPlayer src="/video.mp4" type="video" bindMedia={bindMedia} />)
    await act(async () => {})

    expect(player.setting.find("subtitle")).toBeUndefined()
    expect(await readBlob(mocks.createObjectURL.mock.calls[2][0])).toBe("WEBVTT\n\n")
    expect(mocks.Player.instances).toHaveLength(1)
    expect(player.video.paused).toBe(false)
    expect(player.video.currentTime).toBe(12.5)
  })

  it("finishes consecutive edits in order and skips obsolete queued subtitles", async () => {
    const bindMedia = vi.fn()
    const view = render(<MediaPlayer src="/video.mp4" type="video" bindMedia={bindMedia} />)
    const player = mocks.Player.instances[0]
    let finishLoad: (value: string) => void = () => {}
    player.subtitle.switch.mockImplementationOnce(() => new Promise((resolve) => { finishLoad = resolve }))

    view.rerender(<MediaPlayer src="/video.mp4" type="video" bindMedia={bindMedia} subtitleSrt={originalSrt} />)
    await act(async () => {})
    expect(player.subtitle.switch).toHaveBeenCalledOnce()
    expect(mocks.revokeObjectURL).not.toHaveBeenCalled()
    view.rerender(<MediaPlayer src="/video.mp4" type="video" bindMedia={bindMedia} subtitleSrt={originalSrt.replace("原字幕", "过时字幕")} />)
    view.rerender(<MediaPlayer src="/video.mp4" type="video" bindMedia={bindMedia} subtitleSrt={originalSrt.replace("原字幕", "最终字幕")} />)
    expect(player.subtitle.switch).toHaveBeenCalledOnce()

    await act(async () => { finishLoad("blob:loaded") })
    expect(player.subtitle.switch).toHaveBeenCalledTimes(2)
    expect(await readBlob(mocks.createObjectURL.mock.calls[1][0])).toContain("最终字幕")
    expect(mocks.createObjectURL).toHaveBeenCalledTimes(2)
    expect(mocks.revokeObjectURL).toHaveBeenCalledTimes(2)
    expect(mocks.Player.instances).toHaveLength(1)
  })

  it("releases subtitle blobs after loading and disposes the old player when changing media", async () => {
    const cleanupBinding = vi.fn()
    const bindMedia = vi.fn(() => cleanupBinding)
    const view = render(<MediaPlayer src="/first.mp4" type="video" bindMedia={bindMedia} subtitleSrt={originalSrt} loop />)
    const first = mocks.Player.instances[0]
    first.fullscreenWeb = true
    await act(async () => {})
    expect(mocks.revokeObjectURL).toHaveBeenCalledWith("blob:subtitle-1")

    view.rerender(<MediaPlayer src="/second.mp4" type="video" bindMedia={bindMedia} subtitleSrt={originalSrt} loop />)
    await act(async () => {})
    const second = mocks.Player.instances[1]
    expect(first.destroy).toHaveBeenCalledOnce()
    expect(first.fullscreenWeb).toBe(false)
    expect(cleanupBinding).toHaveBeenCalledOnce()
    expect(bindMedia).toHaveBeenCalledWith(null)
    expect(second.video.src).toContain("/second.mp4")
    expect(second.video.loop).toBe(true)
    expect(second.subtitle.switch).toHaveBeenCalledOnce()
    expect(second.destroy).not.toHaveBeenCalled()

    view.unmount()
    expect(second.destroy).toHaveBeenCalledOnce()
    expect(cleanupBinding).toHaveBeenCalledTimes(2)
  })
})

describe("mobile audio playback", () => {
  beforeEach(() => {
    vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => {})
    vi.spyOn(HTMLMediaElement.prototype, "play").mockImplementation(async function (this: HTMLMediaElement) {
      Object.defineProperty(this, "paused", { configurable: true, value: false })
      fireEvent.play(this)
    })
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(function (this: HTMLMediaElement) {
      Object.defineProperty(this, "paused", { configurable: true, value: true })
      fireEvent.pause(this)
    })
  })

  it("plays, pauses, seeks and changes speed without replacing the audio element", async () => {
    const bindMedia = vi.fn()
    const { container, rerender } = render(<MediaPlayer src="/audio.mp3" type="audio" mobile bindMedia={bindMedia} title="播客" />)
    const audio = container.querySelector("audio")!
    Object.defineProperty(audio, "duration", { configurable: true, value: 60 })
    fireEvent.durationChange(audio)
    fireEvent.click(screen.getByRole("button", { name: "播放音频" }))
    expect(audio.play).toHaveBeenCalledOnce()
    fireEvent.click(screen.getByRole("button", { name: "暂停音频" }))
    expect(audio.pause).toHaveBeenCalledOnce()

    audio.currentTime = 55
    fireEvent.timeUpdate(audio)
    fireEvent.click(screen.getByRole("button", { name: "快进15秒" }))
    expect(audio.currentTime).toBe(60)
    fireEvent.click(screen.getByRole("button", { name: "快退15秒" }))
    expect(audio.currentTime).toBe(45)
    fireEvent.change(screen.getByRole("slider", { name: "音频播放进度" }), { target: { value: "10" } })
    expect(audio.currentTime).toBe(10)
    fireEvent.change(screen.getByRole("combobox", { name: "播放速度" }), { target: { value: "1.5" } })
    expect(audio.playbackRate).toBe(1.5)
    expect((screen.getByRole("combobox", { name: "播放速度" }) as HTMLSelectElement).value).toBe("1.5")

    rerender(<MediaPlayer src="/audio.mp3" type="audio" mobile bindMedia={bindMedia} title="改后的标题" loop />)
    expect(container.querySelector("audio")).toBe(audio)
    expect(audio.currentTime).toBe(10)
    expect(audio.loop).toBe(true)
    expect(bindMedia).toHaveBeenCalledOnce()
  })

  it("reports a rejected play request and offers loop control", async () => {
    vi.mocked(HTMLMediaElement.prototype.play).mockRejectedValueOnce(new Error("blocked"))
    const loop = vi.fn()
    render(<MediaPlayer src="/audio.mp3" type="audio" mobile bindMedia={vi.fn()} onLoopChange={loop} />)
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "播放音频" })))
    expect(screen.getByRole("alert").textContent).toContain("暂时无法播放")
    fireEvent.click(screen.getByRole("button", { name: "开启循环播放" }))
    expect(loop).toHaveBeenCalledWith(true)
  })
})
