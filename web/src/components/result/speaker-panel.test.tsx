/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"
import { SpeakerPanel } from "./speaker-panel"

afterEach(cleanup)

it("counts each speaker's overlapping coverage once and excludes unassigned segments", () => {
  render(<SpeakerPanel duration={30} currentTime={0} onSeek={vi.fn()} subtitles={[
    { index: 1, startTime: 0, endTime: 10000, speaker: "Alice", text: "one" },
    { index: 2, startTime: 5000, endTime: 15000, speaker: "Alice", text: "two" },
    { index: 3, startTime: 15000, endTime: 20000, speaker: "Bob", text: "three" },
    { index: 4, startTime: 20000, endTime: 30000, speaker: "unknown", text: "uncertain" },
  ]} />)
  expect(screen.getByText(/\(75%\)/)).toBeTruthy()
  expect(screen.getByText(/\(25%\)/)).toBeTruthy()
  expect(screen.queryByRole("button", { name: "归属待定" })).toBeNull()
  expect(screen.getByRole("status").textContent).toContain("1 段待定")
})

it("keeps a short anonymous voice visible with a nonzero coverage label", () => {
  render(<SpeakerPanel duration={1000} currentTime={0} onSeek={vi.fn()} subtitles={[
    { index: 1, startTime: 0, endTime: 995000, speaker: "Alice", text: "long" },
    { index: 2, startTime: 995000, endTime: 1000000, speaker: "Unknown-abcd", text: "brief" },
  ]} />)
  expect(screen.getByRole("button", { name: /匿名声音/ })).toBeTruthy()
  expect(screen.getByText(/\(<1%\)/)).toBeTruthy()
})

const conversation = [
  { index: 1, startTime: 0, endTime: 4000, speaker: "瓜熟迪落拉", text: "Why?" },
  { index: 2, startTime: 4000, endTime: 6000, speaker: "unknown", text: "Yeah." },
  { index: 3, startTime: 6000, endTime: 20000, speaker: "Eric Hovagim", text: "Because." },
  { index: 4, startTime: 20000, endTime: 22000, speaker: "unknown", text: "Right." },
  { index: 5, startTime: 22000, endTime: 30000, speaker: "Unknown-6610", text: "And MAGA." },
]

it("steps through pending lines and fills pending lines and anonymous voices on request", () => {
  const onSeek = vi.fn()
  const onAutoFill = vi.fn()
  render(<SpeakerPanel duration={30} currentTime={5} onSeek={onSeek} subtitles={conversation} onAutoFill={onAutoFill} />)
  expect(screen.getByRole("status").textContent).toContain("2 段待定")
  expect(screen.getByRole("status").textContent).toContain("1/2")
  fireEvent.click(screen.getByRole("button", { name: "下一段待定字幕" }))
  expect(onSeek).toHaveBeenLastCalledWith(20000)
  // Inside the first pending line, "previous" returns to its start.
  fireEvent.click(screen.getByRole("button", { name: "上一段待定字幕" }))
  expect(onSeek).toHaveBeenLastCalledWith(4000)
  fireEvent.click(screen.getByRole("button", { name: "自动补全" }))
  expect(onAutoFill).toHaveBeenCalledOnce()
})

it("shows names without source tags and offers to merge a voice that may be the same person", () => {
  const onRename = vi.fn()
  render(<SpeakerPanel duration={30} currentTime={0} onSeek={vi.fn()} subtitles={conversation} onRenameSpeaker={onRename}
    hints={{
      nameSources: { "Eric Hovagim": "标题联名；长篇回答" },
      suggestions: [{ source_label: "Unknown-6610", current_name: "Eric Hovagim", reason: "声音相似度 0.68" }],
      participants: ["瓜熟迪落拉", "Eric Hovagim"],
    }} />)
  expect(screen.queryByText("推断")).toBeNull()
  fireEvent.click(screen.getByRole("button", { name: "声音很像 Eric Hovagim，合并？" }))
  expect(onRename).toHaveBeenCalledWith("Unknown-6610", "Eric Hovagim")
})

it("offers named people when renaming an anonymous voice", () => {
  render(<SpeakerPanel duration={30} currentTime={0} onSeek={vi.fn()} subtitles={conversation} onRenameSpeaker={vi.fn()}
    hints={{ nameSources: {}, suggestions: [], participants: ["瓜熟迪落拉", "张三"] }} />)
  fireEvent.click(screen.getByRole("button", { name: "匿名声音 6610" }))
  const input = screen.getByRole("combobox", { name: "匿名声音 6610 的名字" }) as HTMLInputElement
  expect(input.value).toBe("")
  fireEvent.click(screen.getByRole("button", { name: "展开所有说话人名字" }))
  const options = screen.getAllByRole("option").map((option) => option.textContent)
  expect(options).toEqual(["张三", "瓜熟迪落拉", "Eric Hovagim"])
})

it("says when summary and mind map predate the names and rewrites them", () => {
  const onRegenerate = vi.fn()
  render(<SpeakerPanel duration={30} currentTime={0} onSeek={vi.fn()} subtitles={conversation} stale onRegenerate={onRegenerate} />)
  fireEvent.click(screen.getByRole("button", { name: "更新摘要和导图" }))
  expect(onRegenerate).toHaveBeenCalledOnce()
})
