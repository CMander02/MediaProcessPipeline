/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import "@testing-library/jest-dom/vitest"
import { afterEach, describe, expect, it, vi } from "vitest"
import { open } from "@tauri-apps/plugin-dialog"

import { PathPickerRow, ProxySetting } from "./setting-controls"

vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn(),
  isTauri: vi.fn(() => true),
}))

vi.mock("@tauri-apps/plugin-dialog", () => ({
  open: vi.fn(),
}))

afterEach(() => {
  cleanup()
  vi.mocked(open).mockReset()
  vi.restoreAllMocks()
})

describe("PathPickerRow", () => {
  it("uses the Tauri directory picker before the manual path fallback", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined)
    const prompt = vi.spyOn(window, "prompt").mockReturnValue(null)
    vi.mocked(open).mockResolvedValue("C:\\Models\\Qwen3")

    render(
      <PathPickerRow
        label="模型路径"
        settingKey="qwen3_asr_model_path"
        value=""
        onSave={onSave}
        saving={{}}
        saved={{}}
        title="选择 Qwen3-ASR 模型目录"
      />,
    )

    fireEvent.click(screen.getByRole("button", { name: "选择" }))

    await waitFor(() => {
      expect(open).toHaveBeenCalledWith({
        title: "选择 Qwen3-ASR 模型目录",
        defaultPath: undefined,
        directory: true,
        multiple: false,
        canCreateDirectories: true,
      })
    })
    expect(onSave).toHaveBeenCalledWith("qwen3_asr_model_path", "C:\\Models\\Qwen3")
    expect(prompt).not.toHaveBeenCalled()
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
