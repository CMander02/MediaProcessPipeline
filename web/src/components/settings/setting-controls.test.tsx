/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import "@testing-library/jest-dom/vitest"
import { afterEach, describe, expect, it, vi } from "vitest"

import { PathPickerRow, ProxySetting } from "./setting-controls"

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

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
    expect(mode).toHaveValue("system")

    fireEvent.change(mode, { target: { value: "none" } })
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
    fireEvent.change(screen.getByRole("combobox", { name: "代理模式" }), { target: { value: "custom" } })
    fireEvent.change(screen.getByRole("textbox", { name: "代理地址" }), {
      target: { value: "http://localhost:7897" },
    })
    fireEvent.click(screen.getByRole("button", { name: "保存代理地址" }))

    await waitFor(() =>
      expect(onSave).toHaveBeenLastCalledWith("network_proxy", "http://localhost:7897"),
    )
  })
})
