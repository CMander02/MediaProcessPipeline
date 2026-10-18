/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { AppShell } from "./app-shell"

vi.mock("./desktop-header", () => ({ DesktopHeader: () => null }))
vi.mock("./history-buttons", () => ({ HistoryButtons: () => null }))
vi.mock("./mobile-bottom-nav", () => ({ MobileBottomNav: () => null }))
afterEach(cleanup)

describe("mobile detail navigation", () => {
  it("keeps the full toolbar state when opening and closing it above a playing detail", () => {
    const back = vi.fn()
    render(<AppShell activePage="files" mobileDetail onMobileBack={back} mobileBackLabel="返回列表" runtimeControls={false} toolbar={<input aria-label="搜索文件" defaultValue="course" />}><div>播放内容</div></AppShell>)
    const input = screen.getByRole("textbox", { name: "搜索文件" })
    fireEvent.click(screen.getByRole("button", { name: "返回列表" }))
    expect(back).toHaveBeenCalledOnce()
    fireEvent.click(screen.getByRole("button", { name: "展开顶部工具栏" }))
    expect(screen.queryByRole("navigation", { name: "内容导航" })).toBeNull()
    expect(screen.getByRole("textbox", { name: "搜索文件" })).toBe(input)
    fireEvent.click(screen.getByRole("button", { name: "收起顶部工具栏" }))
    expect(screen.getByRole("navigation", { name: "内容导航" })).toBeTruthy()
    expect(screen.getByRole("textbox", { name: "搜索文件" })).toBe(input)
    expect((input as HTMLInputElement).value).toBe("course")
  })
})
