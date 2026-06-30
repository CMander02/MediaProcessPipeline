/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import "@testing-library/jest-dom/vitest"
import { afterEach, describe, expect, it, vi } from "vitest"
import { open } from "@tauri-apps/plugin-dialog"

import { PathPickerRow } from "./setting-controls"

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
