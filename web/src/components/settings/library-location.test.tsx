// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { LibraryLocation } from "@/components/settings/library-location"

const mocks = vi.hoisted(() => ({
  page: vi.fn(),
  drives: vi.fn(),
  browse: vi.fn(),
  notifySuccess: vi.fn(),
}))
vi.mock("@/hooks/use-app-access-context", () => ({
  useAppAccess: () => ({ online: true, capabilities: { filesystem_browse: true } }),
}))
vi.mock("@/lib/notify", () => ({ notifySuccess: mocks.notifySuccess, notifyError: vi.fn() }))
vi.mock("@/lib/api", () => ({
  api: {
    archives: { page: mocks.page },
    filesystem: { drives: mocks.drives, browse: mocks.browse },
  },
}))

const FOLDERS: Record<string, string[]> = {
  "D:\\": ["Video"],
  "D:\\Video": ["MediaProcessPipeline"],
  "E:\\": ["Media"],
  "E:\\Media": ["MPP Library"],
}

beforeEach(() => {
  mocks.page.mockResolvedValue({ total: 652 })
  mocks.drives.mockResolvedValue({ success: true, drives: [{ name: "E:", path: "E:\\", is_dir: true }] })
  mocks.browse.mockImplementation(async (path: string) => ({
    success: true,
    path,
    items: [
      { name: "..", path: "", is_dir: true, size: null },
      ...(FOLDERS[path] ?? []).map((name) => ({
        name,
        path: `${path.replace(/\\$/, "")}\\${name}`,
        is_dir: true,
        size: null,
      })),
    ],
  }))
})
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

const openDialog = () => fireEvent.click(screen.getByRole("button", { name: "更改…" }))
const switchButton = () => screen.getByRole("button", { name: "切换到这里" })

describe("library location", () => {
  it("shows where the library is and switches only after a deep enough folder is chosen", async () => {
    const onSwitch = vi.fn().mockResolvedValue(undefined)
    render(<LibraryLocation value={"D:\\Video\\MediaProcessPipeline"} onSwitch={onSwitch} />)

    expect(screen.getByText("D:\\Video\\MediaProcessPipeline")).toBeTruthy()
    expect(await screen.findByText("652 个条目")).toBeTruthy()

    openDialog()
    expect(switchButton()).toHaveProperty("disabled", true)
    expect(screen.getByText(/不会移动也不会删除/)).toBeTruthy()
    expect(await screen.findByRole("button", { name: "MediaProcessPipeline" })).toBeTruthy()
    expect(screen.queryByRole("button", { name: ".." })).toBeNull()

    // D:\Video → D:\ → the list of drives
    fireEvent.click(screen.getByRole("button", { name: "上一级" }))
    await waitFor(() => expect(mocks.browse).toHaveBeenLastCalledWith("D:\\", "directory"))
    fireEvent.click(screen.getByRole("button", { name: "上一级" }))
    fireEvent.click(await screen.findByRole("button", { name: "E:" }))

    // A folder right under the drive is refused by the backend, so it can't be picked here either.
    fireEvent.click(await screen.findByRole("button", { name: "Media" }))
    expect(screen.getByText(/至少两层深的文件夹/)).toBeTruthy()
    expect(switchButton()).toHaveProperty("disabled", true)
    expect(screen.queryByText(/想把现在的资料库搬过去/)).toBeNull()

    fireEvent.click(await screen.findByRole("button", { name: "MPP Library" }))
    expect(screen.getByLabelText("新位置")).toHaveProperty("value", "E:\\Media\\MPP Library")
    expect(screen.queryByText(/至少两层深的文件夹/)).toBeNull()
    expect(screen.getByText('mpp storage migrate --target "E:\\Media\\MPP Library" --apply')).toBeTruthy()

    fireEvent.click(switchButton())
    await waitFor(() => expect(onSwitch).toHaveBeenCalledWith("E:\\Media\\MPP Library"))
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
    expect(mocks.notifySuccess).toHaveBeenCalledWith("已切换资料库", "E:\\Media\\MPP Library")
  })

  it("keeps the dialog open with the reason when the switch fails", async () => {
    const onSwitch = vi.fn().mockRejectedValue(new Error("没有写入权限"))
    render(<LibraryLocation value={"D:\\Video\\MediaProcessPipeline"} onSwitch={onSwitch} />)

    openDialog()
    fireEvent.change(screen.getByLabelText("新位置"), { target: { value: "E:\\Media\\MPP" } })
    fireEvent.click(switchButton())

    expect((await screen.findByRole("alert")).textContent).toContain("没有写入权限")
    expect(screen.getByRole("dialog")).toBeTruthy()
    expect(mocks.notifySuccess).not.toHaveBeenCalled()
  })
})
