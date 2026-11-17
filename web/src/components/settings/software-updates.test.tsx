/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import "@testing-library/jest-dom/vitest"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { api } from "@/lib/api"
import type { UpdateCheckResponse } from "@/lib/software-update"
import { PlatformProvider } from "@/platform/platform-context"
import { createWebPlatform } from "@/platform/web-platform"
import { SoftwareUpdates } from "./software-updates"

vi.mock("@/lib/theme", () => ({ watchSystemTheme: () => () => {} }))

const release: UpdateCheckResponse = {
  current_version: "0.9.0", latest_version: "0.10.0", update_available: true,
  release_url: "https://github.com/CMander02/MediaProcessPipeline/releases/tag/v0.10.0",
  published_at: "2026-11-17T00:00:00Z", notes: "新增多设备同步\n修复设备选择",
  assets: [
    { name: "MPP-server-bundle-0.10.0.tar.gz", url: "https://github.com/release/server.tar.gz", size: 123, recommended: true },
    { name: "MPP-Desktop-0.10.0-mac-arm64.dmg", url: "https://github.com/release/mac.dmg", size: 234, recommended: false },
    { name: "SHA256SUMS.txt", url: "https://github.com/release/SHA256SUMS.txt", size: 128, recommended: false },
  ],
}

beforeEach(() => {
  vi.spyOn(api, "health").mockResolvedValue({ status: "ok", version: "0.9.0" })
  vi.spyOn(api.updates, "check").mockResolvedValue(release)
  vi.spyOn(api.updates, "download").mockResolvedValue({ path: "/data/tmp/updates/0.10.0/package.tar.gz", sha256: "a".repeat(64), asset_name: release.assets[0].name, version: "0.10.0", instructions: "停止后端后解压更新包。" })
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe("software updates", () => {
  it("reads backend version and displays a newer release with notes and recommended packages", async () => {
    render(<SoftwareUpdates mobile={false} />)
    await screen.findByText("当前后端版本：0.9.0")
    fireEvent.click(screen.getByRole("button", { name: "检查更新" }))
    await screen.findByText("发现新版本：0.10.0")
    expect(api.updates.check).toHaveBeenCalledWith(true)
    expect(screen.getByText(/新增多设备同步/)).toHaveTextContent("修复设备选择")
    expect(screen.getByRole("combobox", { name: "更新包" })).toHaveValue(release.assets[0].name)
    expect(screen.queryByRole("option", { name: "SHA256SUMS.txt" })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "下载并校验更新包" }))
    await screen.findByText("更新包已通过 SHA-256 校验。")
    expect(api.updates.download).toHaveBeenCalledWith(release.assets[0].name)
    expect(screen.getByText(/文件路径：/)).toHaveTextContent("/data/tmp/updates/0.10.0/package.tar.gz")
    expect(screen.getByText("停止后端后解压更新包。")).toBeInTheDocument()
  })

  it("downloads an explicitly selected asset", async () => {
    render(<SoftwareUpdates mobile={false} />)
    fireEvent.click(screen.getByRole("button", { name: "检查更新" }))
    const select = await screen.findByRole("combobox", { name: "更新包" })
    fireEvent.change(select, { target: { value: release.assets[1].name } })
    fireEvent.click(screen.getByRole("button", { name: "下载并校验更新包" }))
    await waitFor(() => expect(api.updates.download).toHaveBeenCalledWith(release.assets[1].name))
  })

  it("reports the current release without offering a download", async () => {
    vi.mocked(api.updates.check).mockResolvedValue({ ...release, update_available: false, latest_version: "0.9.0" })
    render(<SoftwareUpdates mobile={false} />)
    fireEvent.click(screen.getByRole("button", { name: "检查更新" }))
    await screen.findByText("当前已是最新版本：0.9.0")
    expect(screen.queryByRole("button", { name: "下载并校验更新包" })).not.toBeInTheDocument()
  })

  it("reports check and checksum errors and permits retry", async () => {
    vi.mocked(api.updates.check).mockRejectedValueOnce(new Error("无法连接 GitHub，请检查网络连接"))
    vi.mocked(api.updates.download).mockRejectedValueOnce(new Error("SHA-256 校验失败"))
    render(<SoftwareUpdates mobile={false} />)
    fireEvent.click(screen.getByRole("button", { name: "检查更新" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("无法连接 GitHub")
    fireEvent.click(screen.getByRole("button", { name: "检查更新" }))
    const download = await screen.findByRole("button", { name: "下载并校验更新包" })
    fireEvent.click(download)
    expect(await screen.findByRole("alert")).toHaveTextContent("SHA-256 校验失败")
    expect(download).toBeEnabled()
    fireEvent.click(download)
    await screen.findByText("更新包已通过 SHA-256 校验。")
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
  })

  it("blocks concurrent checks and downloads", async () => {
    let resolveCheck: ((value: UpdateCheckResponse) => void) | undefined
    vi.mocked(api.updates.check).mockReturnValue(new Promise((resolve) => { resolveCheck = resolve }))
    render(<SoftwareUpdates mobile={false} />)
    const check = screen.getByRole("button", { name: "检查更新" })
    fireEvent.click(check)
    fireEvent.click(check)
    expect(check).toBeDisabled()
    expect(api.updates.check).toHaveBeenCalledOnce()
    resolveCheck?.(release)
    const download = await screen.findByRole("button", { name: "下载并校验更新包" })
    fireEvent.click(download)
    fireEvent.click(download)
    expect(download).toBeDisabled()
    expect(screen.getByRole("button", { name: "检查更新" })).toBeDisabled()
    await screen.findByText("更新包已通过 SHA-256 校验。")
    expect(api.updates.download).toHaveBeenCalledOnce()
  })

  it.each([false, true])("opens Release externally on mobile (native: %s)", async (native) => {
    const platform = { ...createWebPlatform(), isNative: native, openExternal: vi.fn().mockResolvedValue(undefined) }
    render(<PlatformProvider adapter={platform}><SoftwareUpdates mobile={!native} /></PlatformProvider>)
    fireEvent.click(screen.getByRole("button", { name: "检查更新" }))
    const link = await screen.findByRole("link", { name: "前往 GitHub Release 下载" })
    expect(link).toHaveAttribute("href", release.release_url)
    expect(link).toHaveAttribute("rel", "noopener noreferrer")
    expect(screen.queryByRole("button", { name: "下载并校验更新包" })).not.toBeInTheDocument()
    fireEvent.click(link)
    expect(platform.openExternal).toHaveBeenCalledWith(release.release_url)
    expect(platform.openExternal).toHaveBeenCalledOnce()
    expect(api.updates.download).not.toHaveBeenCalled()
  })

  it("offers the external download when no platform package is recommended", async () => {
    vi.mocked(api.updates.check).mockResolvedValue({ ...release, assets: [] })
    render(<SoftwareUpdates mobile={false} />)
    fireEvent.click(screen.getByRole("button", { name: "检查更新" }))
    await screen.findByRole("link", { name: "前往 GitHub Release 下载" })
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument()
  })
})
