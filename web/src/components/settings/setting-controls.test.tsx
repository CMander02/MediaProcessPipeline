/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import "@testing-library/jest-dom/vitest"
import { afterEach, describe, expect, it, vi } from "vitest"

import { SettingsSaveErrors } from "./save-errors"
import { PathPickerRow, ProxySetting, SettingRow } from "./setting-controls"

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

// Radix selects open from the keyboard in jsdom and render their options only while open.
function choose(trigger: HTMLElement, option: string) {
  Element.prototype.scrollIntoView ??= () => {}
  fireEvent.keyDown(trigger, { key: "ArrowDown" })
  fireEvent.click(screen.getByRole("option", { name: option }))
}

describe("PathPickerRow", () => {
  it("saves a manually entered server directory", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined)
    const prompt = vi.spyOn(window, "prompt").mockReturnValue("C:\\Models\\Qwen3")

    render(
      <PathPickerRow
        label="模型路径"
        settingKey="sherpa_model_root"
        value=""
        onSave={onSave}
        saving={{}}
        saved={{}}
        title="选择 sherpa-onnx 模型根目录"
      />,
    )

    fireEvent.click(screen.getByRole("button", { name: "选择" }))

    await waitFor(() => {
      expect(prompt).toHaveBeenCalledWith("选择 sherpa-onnx 模型根目录", "")
    })
    expect(onSave).toHaveBeenCalledWith("sherpa_model_root", "C:\\Models\\Qwen3")
  })
})

describe("ProxySetting", () => {
  it("maps system, none, and custom modes to the existing proxy setting", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined)
    const { rerender } = render(
      <ProxySetting
        label="代理"
        settingKey="network_proxy"
        value=""
        onSave={onSave}
        saving={{}}
        saved={{}}
      />,
    )

    const mode = screen.getByRole("combobox", { name: "代理模式" })
    expect(mode).toHaveTextContent("系统代理")

    choose(mode, "无代理")
    await waitFor(() => expect(onSave).toHaveBeenLastCalledWith("network_proxy", "direct"))

    rerender(
      <ProxySetting
        label="代理"
        settingKey="network_proxy"
        value="direct"
        onSave={onSave}
        saving={{}}
        saved={{}}
      />,
    )
    choose(screen.getByRole("combobox", { name: "代理模式" }), "自定义")
    const address = screen.getByRole("textbox", { name: "代理地址" })
    fireEvent.change(address, { target: { value: "http://localhost:7897" } })
    fireEvent.blur(address)

    await waitFor(() =>
      expect(onSave).toHaveBeenLastCalledWith("network_proxy", "http://localhost:7897"),
    )
  })

  it("says an empty value follows the global proxy, and what that is, when it inherits", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined)
    const { rerender } = render(
      <ProxySetting
        label="代理"
        settingKey="youtube_proxy"
        value=""
        inheritFrom={{ where: "存储与网络 › 网络", value: "http://user:secret@127.0.0.1:7897" }}
        onSave={onSave}
        saving={{}}
        saved={{}}
      />,
    )

    const mode = screen.getByRole("combobox", { name: "代理模式" })
    expect(mode).toHaveTextContent("跟随全局（http://127.0.0.1:7897）")
    expect(mode).not.toHaveTextContent("secret")
    expect(screen.getByText(/全局代理在「存储与网络 › 网络」里设置/)).toBeInTheDocument()

    choose(mode, "无代理")
    await waitFor(() => expect(onSave).toHaveBeenLastCalledWith("youtube_proxy", "direct"))

    rerender(
      <ProxySetting
        label="代理"
        settingKey="youtube_proxy"
        value="direct"
        inheritFrom={{ where: "存储与网络 › 网络", value: "" }}
        onSave={onSave}
        saving={{}}
        saved={{}}
      />,
    )
    choose(screen.getByRole("combobox", { name: "代理模式" }), "跟随全局（系统代理）")
    await waitFor(() => expect(onSave).toHaveBeenLastCalledWith("youtube_proxy", ""))
  })
})

describe("SettingRow", () => {
  const row = (onSave: (key: string, value: unknown) => Promise<void>, value = "30", masked = false) => (
    <SettingRow label="超时" settingKey="web_scrape_timeout" value={value} onSave={onSave} saving={{}} saved={{}} masked={masked} />
  )

  it("saves a change on blur or Enter, and only once", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined)
    render(row(onSave))
    const field = screen.getByRole("textbox", { name: "超时" })

    fireEvent.blur(field)
    expect(onSave).not.toHaveBeenCalled()

    fireEvent.change(field, { target: { value: "45" } })
    fireEvent.keyDown(field, { key: "Enter" })
    fireEvent.blur(field)
    await waitFor(() => expect(onSave).toHaveBeenCalledWith("web_scrape_timeout", "45"))
    expect(onSave).toHaveBeenCalledTimes(1)
  })

  it("puts the saved value back on Escape", () => {
    const onSave = vi.fn().mockResolvedValue(undefined)
    render(row(onSave))
    const field = screen.getByRole("textbox", { name: "超时" })

    fireEvent.change(field, { target: { value: "45" } })
    fireEvent.keyDown(field, { key: "Escape" })
    expect(field).toHaveValue("30")
    fireEvent.blur(field)
    expect(onSave).not.toHaveBeenCalled()
  })

  it("says 已保存 next to the field and undoes back to the old value", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined)
    const { rerender } = render(row(onSave))
    const field = screen.getByRole("textbox", { name: "超时" })

    fireEvent.change(field, { target: { value: "45" } })
    fireEvent.blur(field)
    await waitFor(() => expect(onSave).toHaveBeenCalledWith("web_scrape_timeout", "45"))
    rerender(row(onSave, "45"))

    expect(await screen.findByRole("status")).toHaveTextContent("已保存")
    fireEvent.click(screen.getByRole("button", { name: "撤销" }))
    expect(onSave).toHaveBeenLastCalledWith("web_scrape_timeout", "30")
    expect(screen.queryByRole("status")).not.toBeInTheDocument()
  })

  it("offers no undo for a masked secret", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined)
    const { container } = render(row(onSave, "sk-****", true))
    const field = container.querySelector("input") as HTMLInputElement

    fireEvent.change(field, { target: { value: "sk-new" } })
    fireEvent.blur(field)
    expect(await screen.findByRole("status")).toHaveTextContent("已保存")
    expect(screen.queryByRole("button", { name: "撤销" })).not.toBeInTheDocument()
  })

  it("shows why a save failed under the field instead of 已保存", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined)
    render(
      <SettingsSaveErrors.Provider value={{ web_scrape_timeout: "超时必须是正整数" }}>
        {row(onSave)}
      </SettingsSaveErrors.Provider>,
    )
    const field = screen.getByRole("textbox", { name: "超时" })

    fireEvent.change(field, { target: { value: "-1" } })
    fireEvent.blur(field)
    await waitFor(() => expect(onSave).toHaveBeenCalled())
    expect(screen.getByText("没保存上：超时必须是正整数")).toBeInTheDocument()
    expect(screen.queryByRole("status")).not.toBeInTheDocument()
    expect(field).toHaveValue("-1")
  })
})
