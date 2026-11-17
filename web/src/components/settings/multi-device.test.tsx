/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import "@testing-library/jest-dom/vitest"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { api, type ExecutionNodesResponse } from "@/lib/api"
import { MultiDeviceSettings } from "./multi-device"

const registry: ExecutionNodesResponse = {
  enabled: true, preferred_worker_id: "pc", offline_policy: "fallback_server",
  nodes: [
    { node_id: "local", display_name: "服务器", platform: "linux", compute_devices: ["cpu"], capabilities: {}, busy: false, online: true, last_heartbeat_at: 100, updated_at: 100 },
    { node_id: "pc", display_name: "台式机", platform: "windows", compute_devices: ["cpu", "cuda"], capabilities: {}, busy: true, online: true, last_heartbeat_at: 100, updated_at: 100 },
    { node_id: "mac", display_name: "笔记本", platform: "macos", compute_devices: ["cpu", "metal"], capabilities: {}, busy: false, online: false, last_heartbeat_at: 1, updated_at: 1 },
  ],
}
const updateSetting = vi.fn().mockResolvedValue(undefined)
const props = { settings: { remote_api_token: "***...abcd", remote_worker_id: "pc" }, updateSetting, saving: {}, saved: {}, mobile: false }

beforeEach(() => {
  vi.spyOn(api.execution, "nodes").mockResolvedValue(registry)
  vi.spyOn(api.sync, "pull").mockResolvedValue({ enabled: true, upserted: 2, deleted: 1, skipped: 3 })
})
afterEach(() => { cleanup(); vi.restoreAllMocks(); updateSetting.mockClear() })

describe("multi-device settings", () => {
  it("saves server credentials, local identity and synchronization switches", async () => {
    render(<MultiDeviceSettings {...props} />)
    const server = screen.getByRole("textbox", { name: "服务器地址" })
    fireEvent.change(server, { target: { value: "https://mpp.example.com" } })
    fireEvent.blur(server)
    const token = screen.getByLabelText("服务器 Token")
    fireEvent.blur(token)
    expect(updateSetting).not.toHaveBeenCalledWith("remote_api_token", "***...abcd")
    fireEvent.change(token, { target: { value: "new-token" } })
    fireEvent.blur(token)
    const name = screen.getByRole("textbox", { name: "本机节点名称" })
    fireEvent.change(name, { target: { value: "办公电脑" } })
    fireEvent.blur(name)
    const nodeId = screen.getByRole("textbox", { name: "本机节点 ID" })
    fireEvent.change(nodeId, { target: { value: "office-pc" } })
    fireEvent.blur(nodeId)
    for (const label of ["启用归档同步", "上传处理结果", "下载文本归档", "本机领取远程任务"]) fireEvent.click(screen.getByRole("switch", { name: label }))
    await waitFor(() => expect(updateSetting).toHaveBeenCalledWith("remote_server_url", "https://mpp.example.com"))
    expect(updateSetting).toHaveBeenCalledWith("remote_api_token", "new-token")
    expect(updateSetting).toHaveBeenCalledWith("remote_worker_name", "办公电脑")
    expect(updateSetting).toHaveBeenCalledWith("remote_worker_id", "office-pc")
    expect(updateSetting).toHaveBeenCalledWith("remote_sync_enabled", true)
    expect(updateSetting).toHaveBeenCalledWith("remote_sync_upload_results", false)
    expect(updateSetting).toHaveBeenCalledWith("remote_sync_download_results", false)
    expect(updateSetting).toHaveBeenCalledWith("remote_execution_worker_enabled", true)
  })

  it("shows hardware and online state, saves routing and pulls archives", async () => {
    render(<MultiDeviceSettings {...props} />)
    const nodes = screen.getByRole("list", { name: "已注册算力节点" })
    await within(nodes).findByText("台式机")
    expect(within(nodes).getByText("在线 · 忙碌")).toBeInTheDocument()
    expect(within(nodes).getByText(/CPU \/ CUDA/)).toBeInTheDocument()
    expect(within(nodes).getByText("离线")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("switch", { name: "允许远程节点执行任务" }))
    fireEvent.change(screen.getByRole("combobox", { name: "默认优先节点" }), { target: { value: "mac" } })
    fireEvent.change(screen.getByRole("combobox", { name: "目标节点离线时" }), { target: { value: "wait" } })
    expect(updateSetting).toHaveBeenCalledWith("remote_execution_enabled", true)
    expect(updateSetting).toHaveBeenCalledWith("remote_preferred_worker_id", "mac")
    expect(updateSetting).toHaveBeenCalledWith("remote_execution_offline_policy", "wait")
    fireEvent.click(screen.getByRole("button", { name: "立即拉取同步" }))
    await screen.findByText("同步完成：更新 2 项，删除 1 项，跳过 3 项。")
    expect(api.sync.pull).toHaveBeenCalledOnce()
  })

  it("reports pull errors and allows retry", async () => {
    vi.mocked(api.sync.pull).mockRejectedValueOnce(new Error("SHA-256 校验失败"))
    render(<MultiDeviceSettings {...props} />)
    fireEvent.click(screen.getByRole("button", { name: "立即拉取同步" }))
    await screen.findByText("SHA-256 校验失败")
    fireEvent.click(screen.getByRole("button", { name: "立即拉取同步" }))
    await screen.findByText(/同步完成：/)
  })

  it("hides local worker and synchronization controls on phones", async () => {
    render(<MultiDeviceSettings {...props} mobile />)
    await within(screen.getByRole("list", { name: "已注册算力节点" })).findByText("台式机")
    expect(screen.queryByLabelText("本机节点 ID")).not.toBeInTheDocument()
    expect(screen.queryByRole("switch", { name: "本机领取远程任务" })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "立即拉取同步" })).not.toBeInTheDocument()
    expect(screen.getByRole("combobox", { name: "默认优先节点" })).toBeInTheDocument()
  })
})
