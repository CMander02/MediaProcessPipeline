/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { SpeakerNameEditor } from "./speaker-name-editor"
import { SpeakerPanel } from "./speaker-panel"

afterEach(cleanup)

function renderEditor() {
  const onSave = vi.fn()
  const onCancel = vi.fn()
  const onKeyDown = vi.fn()
  render(<div onKeyDown={onKeyDown}>
    <SpeakerNameEditor initialValue="洪乐潼" label="编辑名字" options={["张三", "李四"]} color="purple" onSave={onSave} onCancel={onCancel} />
    <button type="button">其他操作</button>
  </div>)
  return { input: screen.getByRole("combobox") as HTMLInputElement, onSave, onCancel, onKeyDown }
}

describe("speaker name editor", () => {
  it("opens all names with the existing name intact and selects a different name", () => {
    const { input, onSave } = renderEditor()
    const arrow = screen.getByRole("button", { name: "展开所有说话人名字" })
    fireEvent.pointerDown(arrow)
    fireEvent.click(arrow)
    expect(input.value).toBe("洪乐潼")
    expect(document.activeElement).toBe(input)
    expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual(["张三", "李四"])
    const option = screen.getByRole("option", { name: "李四" })
    fireEvent.pointerDown(option)
    fireEvent.click(option)
    fireEvent.blur(input)
    expect(onSave).toHaveBeenCalledExactlyOnceWith("李四")
  })

  it("saves a freely typed name on Enter and contains keyboard events", () => {
    const { input, onSave, onKeyDown } = renderEditor()
    fireEvent.change(input, { target: { value: "  王五  " } })
    fireEvent.keyDown(input, { key: "Enter" })
    fireEvent.blur(input)
    expect(onSave).toHaveBeenCalledExactlyOnceWith("王五")
    expect(onKeyDown).not.toHaveBeenCalled()
  })

  it("commits the typed name when focus leaves the editor", () => {
    const { input, onSave } = renderEditor()
    fireEvent.change(input, { target: { value: "赵六" } })
    fireEvent.blur(input, { relatedTarget: screen.getByRole("button", { name: "其他操作" }) })
    expect(onSave).toHaveBeenCalledExactlyOnceWith("赵六")
  })

  it("cancels on Escape without saving the draft on blur", () => {
    const { input, onSave, onCancel } = renderEditor()
    fireEvent.change(input, { target: { value: "未保存" } })
    fireEvent.keyDown(input, { key: "Escape" })
    fireEvent.blur(input)
    expect(onCancel).toHaveBeenCalledOnce()
    expect(onSave).not.toHaveBeenCalled()
  })

  it("uses arrow keys to choose another name without clearing the input", () => {
    const { input, onSave } = renderEditor()
    fireEvent.keyDown(input, { key: "ArrowDown" })
    fireEvent.keyDown(input, { key: "ArrowDown" })
    fireEvent.keyDown(input, { key: "Enter" })
    expect(onSave).toHaveBeenCalledExactlyOnceWith("李四")
  })

  it("does not submit or cancel while a Chinese input method is composing", () => {
    const { input, onSave, onCancel } = renderEditor()
    fireEvent.click(screen.getByRole("button", { name: "展开所有说话人名字" }))
    fireEvent.compositionStart(input)
    fireEvent.change(input, { target: { value: "张" } })
    fireEvent.keyDown(input, { key: "Enter" })
    fireEvent.keyDown(input, { key: "Escape" })
    expect(onSave).not.toHaveBeenCalled()
    expect(onCancel).not.toHaveBeenCalled()
    fireEvent.compositionEnd(input)
    fireEvent.keyDown(input, { key: "Enter", keyCode: 229 })
    expect(onSave).not.toHaveBeenCalled()
    fireEvent.keyDown(input, { key: "Enter" })
    expect(onSave).toHaveBeenCalledExactlyOnceWith("张")
  })
})

it("renames an existing speaker through the dropdown without seeking the media", () => {
  const onRenameSpeaker = vi.fn()
  const onSeek = vi.fn()
  render(<SpeakerPanel subtitles={[
    { index: 1, text: "第一句", speaker: "洪乐潼", startTime: 0, endTime: 4000 },
    { index: 2, text: "第二句", speaker: "张三", startTime: 4000, endTime: 8000 },
  ]} duration={8} currentTime={3} onSeek={onSeek} onRenameSpeaker={onRenameSpeaker} />)
  fireEvent.click(screen.getByRole("button", { name: "洪乐潼" }))
  fireEvent.click(screen.getByRole("button", { name: "展开所有说话人名字" }))
  fireEvent.click(screen.getByRole("option", { name: "张三" }))
  expect(onRenameSpeaker).toHaveBeenCalledExactlyOnceWith("洪乐潼", "张三")
  expect(onSeek).not.toHaveBeenCalled()
  expect(screen.queryByRole("combobox")).toBeNull()
})

it("displays a speaker name without an extra source or correction tag", () => {
  const sourceInfo = { hints: { nameSources: { "洪乐潼": "来源线索" }, suggestions: [], participants: [] } }
  render(<SpeakerPanel {...sourceInfo} subtitles={[
    { index: 1, text: "第一句", speaker: "洪乐潼", startTime: 0, endTime: 4000 },
  ]} duration={4} currentTime={0} onSeek={vi.fn()} />)
  expect(screen.getByRole("button", { name: "洪乐潼" })).toBeTruthy()
  expect(screen.queryByText("推断")).toBeNull()
  expect(screen.queryByText("人工调整")).toBeNull()
})
