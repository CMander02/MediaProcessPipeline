/** @vitest-environment jsdom */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import "@testing-library/jest-dom/vitest"
import { afterEach, beforeEach, expect, it, vi } from "vitest"
import { BilibiliLogin } from "./bilibili-login"
import { api } from "@/lib/api"

vi.mock("@/lib/api", () => ({ api: { bilibili: { generateQr: vi.fn(), pollQr: vi.fn() } } }))

beforeEach(() => {
  vi.useFakeTimers()
  vi.mocked(api.bilibili.generateQr).mockResolvedValue({ session_id: "session", image: "data:image/svg+xml;base64,PHN2Zy8+", expires_in: 180 })
})
afterEach(() => { cleanup(); vi.useRealTimers(); vi.resetAllMocks() })

it("shows scan confirmation and refreshes account after successful login", async () => {
  const onSuccess = vi.fn().mockResolvedValue(undefined)
  vi.mocked(api.bilibili.pollQr)
    .mockResolvedValueOnce({ state: "scanned", message: "请在手机确认" })
    .mockResolvedValueOnce({ state: "success", message: "登录成功" })
  render(<BilibiliLogin onSuccess={onSuccess} />)
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "扫码登录" })) })
  expect(screen.getByAltText("哔哩哔哩登录二维码")).toBeVisible()
  await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
  expect(screen.getByRole("status")).toHaveTextContent("请在手机确认")
  await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
  expect(onSuccess).toHaveBeenCalledTimes(1)
  expect(screen.queryByRole("img")).not.toBeInTheDocument()
  await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
  expect(api.bilibili.pollQr).toHaveBeenCalledTimes(2)
})

it("stops polling when cancelled or unmounted", async () => {
  const { unmount } = render(<BilibiliLogin onSuccess={vi.fn()} />)
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "扫码登录" })) })
  fireEvent.click(screen.getByRole("button", { name: "取消" }))
  await act(async () => { await vi.advanceTimersByTimeAsync(6000) })
  expect(api.bilibili.pollQr).not.toHaveBeenCalled()
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "扫码登录" })) })
  unmount()
  await act(async () => { await vi.advanceTimersByTimeAsync(6000) })
  expect(api.bilibili.pollQr).not.toHaveBeenCalled()
})

it("clears an expired QR code and allows a new scan", async () => {
  vi.mocked(api.bilibili.pollQr).mockResolvedValue({ state: "expired", message: "二维码已过期" })
  render(<BilibiliLogin onSuccess={vi.fn()} />)
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "扫码登录" })) })
  await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
  expect(screen.queryByRole("img")).not.toBeInTheDocument()
  expect(screen.getByRole("status")).toHaveTextContent("二维码已过期")
  expect(screen.getByRole("button", { name: "扫码登录" })).toBeEnabled()
})
