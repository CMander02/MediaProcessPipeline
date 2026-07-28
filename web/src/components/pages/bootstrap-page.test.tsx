/** @vitest-environment jsdom */

import "@testing-library/jest-dom/vitest"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import App from "@/App"
import { isBootstrapEntry } from "@/lib/bootstrap-entry"
import {
  getBootstrapBridge,
  type BootstrapBridge,
  type BootstrapDiagnostics,
  type BootstrapPhase,
  type BootstrapPreflight,
  type BootstrapPreflightComponent,
  type BootstrapPreflightOverallStatus,
  type BootstrapStatus,
} from "@/lib/tauri"

vi.mock("@/lib/tauri", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/tauri")>()
  return {
    ...actual,
    getBootstrapBridge: vi.fn(),
  }
})

const getBootstrapBridgeMock = vi.mocked(getBootstrapBridge)

function bootstrapStatus(
  phase: BootstrapPhase,
  overrides: Partial<BootstrapStatus> = {},
): BootstrapStatus {
  const state = phase === "APP_READY"
    ? "running"
    : phase === "FAILED_RETRYABLE" || phase === "FAILED_MANUAL"
      ? "error"
      : phase === "READY_TO_START"
        ? "stopped"
        : "starting"

  return {
    state,
    command: "mpp-runtime run",
    cwd: "C:\\Program Files\\MediaProcessPipeline",
    pid: null,
    url: "http://localhost:18000",
    message: phase,
    phase,
    error_code: null,
    component_id: null,
    remediation: null,
    local_path: null,
    ...overrides,
  }
}

function preflightComponent(
  overrides: Partial<BootstrapPreflightComponent> = {},
): BootstrapPreflightComponent {
  return {
    component_id: "desktop-runtime",
    label: "桌面运行时",
    status: "ready",
    required: true,
    version: "1.0.0",
    path: "C:\\Program Files\\MediaProcessPipeline\\runtime",
    error_code: null,
    remediation: null,
    detail: null,
    ...overrides,
  }
}

function preflightReport(
  overallStatus: BootstrapPreflightOverallStatus = "ready",
  components: BootstrapPreflightComponent[] = [preflightComponent()],
): BootstrapPreflight {
  return {
    schema_version: 1,
    overall_status: overallStatus,
    components,
  }
}

function createBridge(
  initial: BootstrapStatus,
  preflight: BootstrapPreflight = preflightReport(),
) {
  let statusListener: ((status: BootstrapStatus) => void) | undefined
  const diagnostics: BootstrapDiagnostics = {
    status: initial,
    logs: [
      {
        ts: "2026-07-28T09:00:00.000Z",
        source: "system",
        line: "Desktop runtime manifest verified.",
      },
    ],
  }
  const unsubscribe = vi.fn()
  const bridge: BootstrapBridge = {
    getStatus: vi.fn().mockResolvedValue(initial),
    getPreflight: vi.fn().mockResolvedValue(preflight),
    retry: vi.fn().mockResolvedValue(bootstrapStatus("STARTING_BACKEND")),
    openLogs: vi.fn().mockResolvedValue(undefined),
    getDiagnostics: vi.fn().mockResolvedValue(diagnostics),
    onStatus: vi.fn((listener) => {
      statusListener = listener
      return unsubscribe
    }),
  }

  return {
    bridge,
    emit(status: BootstrapStatus) {
      act(() => statusListener?.(status))
    },
    unsubscribe,
  }
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  vi.unstubAllGlobals()
  window.history.replaceState({}, "", "/")
})

describe("bootstrap entry", () => {
  it("only selects the bootstrap page for bootstrap=1", () => {
    expect(isBootstrapEntry("?bootstrap=1")).toBe(true)
    expect(isBootstrapEntry("?source=desktop&bootstrap=1")).toBe(true)
    expect(isBootstrapEntry("?bootstrap=0")).toBe(false)
    expect(isBootstrapEntry("")).toBe(false)
  })
})

describe("BootstrapPage", () => {
  it("renders through App without sending HTTP requests before the backend is ready", async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal("fetch", fetchMock)
    const harness = createBridge(bootstrapStatus("SCANNING"))
    getBootstrapBridgeMock.mockReturnValue(harness.bridge)
    window.history.replaceState({}, "", "/?bootstrap=1")

    render(<App />)

    expect(await screen.findByText("正在检查本地运行环境")).toBeInTheDocument()
    expect(screen.getByRole("progressbar", { name: "启动进度 15%" })).toBeInTheDocument()

    harness.emit(bootstrapStatus("READY_TO_START"))
    expect(screen.getByText("运行环境已准备完成")).toBeInTheDocument()
    harness.emit(bootstrapStatus("STARTING_BACKEND"))
    expect(screen.getByText("正在启动本地服务")).toBeInTheDocument()
    harness.emit(bootstrapStatus("WAITING_HEALTH"))
    expect(screen.getByText("正在确认服务状态")).toBeInTheDocument()
    harness.emit(bootstrapStatus("FAILED_RETRYABLE"))
    expect(screen.getByText("启动遇到可恢复问题")).toBeInTheDocument()
    harness.emit(bootstrapStatus("FAILED_MANUAL"))
    expect(screen.getByText("需要完成本地处理")).toBeInTheDocument()
    harness.emit(bootstrapStatus("APP_READY"))
    expect(screen.getByText("应用已就绪")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "进入应用" })).toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it("summarizes a ready preflight and its required components", async () => {
    const harness = createBridge(
      bootstrapStatus("READY_TO_START"),
      preflightReport("ready", [
        preflightComponent(),
        preflightComponent({
          component_id: "ffmpeg",
          label: "FFmpeg",
          version: "7.1",
        }),
      ]),
    )
    getBootstrapBridgeMock.mockReturnValue(harness.bridge)
    window.history.replaceState({}, "", "/?bootstrap=1")

    render(<App />)

    expect(await screen.findByText("运行环境检查通过", { exact: false })).toBeInTheDocument()
    expect(screen.getByText("已通过")).toBeInTheDocument()
    expect(screen.getByText("2/2 项必需组件就绪", { exact: false })).toBeInTheDocument()
    expect(screen.getByText("FFmpeg")).toBeInTheDocument()
    expect(screen.getAllByText("已就绪")).toHaveLength(2)
  })

  it("shows a missing component and its detailed repair fields", async () => {
    const harness = createBridge(
      bootstrapStatus("FAILED_MANUAL"),
      preflightReport("needs_repair", [
        preflightComponent({
          status: "missing",
          version: null,
          path: null,
          error_code: "RUNTIME_MISSING",
          remediation: "重新安装桌面运行时。",
          detail: "runtime-manifest.json 未找到。",
        }),
      ]),
    )
    getBootstrapBridgeMock.mockReturnValue(harness.bridge)
    window.history.replaceState({}, "", "/?bootstrap=1")

    render(<App />)

    expect(await screen.findByText("需要修复本地组件", { exact: false })).toBeInTheDocument()
    expect(screen.getByText("组件缺失")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "展开诊断" }))

    expect(await screen.findByText("RUNTIME_MISSING")).toBeInTheDocument()
    expect(screen.getByText("重新安装桌面运行时。")).toBeInTheDocument()
    expect(screen.getByText("runtime-manifest.json 未找到。")).toBeInTheDocument()
  })

  it("shows configuration-invalid preflight results", async () => {
    const harness = createBridge(
      bootstrapStatus("FAILED_MANUAL"),
      preflightReport("needs_configuration", [
        preflightComponent({
          component_id: "data-root",
          label: "数据目录",
          status: "invalid",
          version: null,
          path: "D:\\MPP Data",
          error_code: "DATA_ROOT_INVALID",
          remediation: "选择可写的数据目录。",
          detail: "当前目录不可写。",
        }),
      ]),
    )
    getBootstrapBridgeMock.mockReturnValue(harness.bridge)
    window.history.replaceState({}, "", "/?bootstrap=1")

    render(<App />)

    expect(await screen.findByText("需要完成运行配置", { exact: false })).toBeInTheDocument()
    expect(screen.getByText("待配置")).toBeInTheDocument()
    expect(screen.getByText("配置无效")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "展开诊断" }))

    expect(await screen.findByText("DATA_ROOT_INVALID")).toBeInTheDocument()
    expect(screen.getByText("D:\\MPP Data")).toBeInTheDocument()
  })

  it("shows stable failure fields and supports retry, logs, and diagnostics", async () => {
    const failed = bootstrapStatus("FAILED_RETRYABLE", {
      message: "桌面代理端口已被其他进程占用。",
      error_code: "PRIVATE_PORT_UNAVAILABLE",
      component_id: "backend-port",
      remediation: "关闭冲突的本地服务后重试。",
      local_path: "C:\\Users\\demo\\AppData\\Local\\MPP\\logs",
    })
    const harness = createBridge(failed)
    getBootstrapBridgeMock.mockReturnValue(harness.bridge)
    window.history.replaceState({}, "", "/?bootstrap=1")

    render(<App />)

    expect(await screen.findByRole("alert")).toHaveTextContent("PRIVATE_PORT_UNAVAILABLE")
    expect(screen.getByText("桌面代理端口已被其他进程占用。")).toBeInTheDocument()
    expect(screen.getByText("backend-port")).toBeInTheDocument()
    expect(screen.getByText("关闭冲突的本地服务后重试。")).toBeInTheDocument()
    expect(screen.getByText("C:\\Users\\demo\\AppData\\Local\\MPP\\logs")).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "重试" }))
    await waitFor(() => expect(harness.bridge.retry).toHaveBeenCalledTimes(1))
    expect(await screen.findByText("正在启动本地服务")).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "打开日志目录" }))
    await waitFor(() => expect(harness.bridge.openLogs).toHaveBeenCalledTimes(1))

    const diagnosticsButton = screen.getByRole("button", { name: "展开诊断" })
    expect(diagnosticsButton).toHaveAttribute("aria-expanded", "false")
    fireEvent.click(diagnosticsButton)

    await waitFor(() => expect(harness.bridge.getDiagnostics).toHaveBeenCalledTimes(1))
    expect(screen.getByRole("button", { name: "收起诊断" })).toHaveAttribute("aria-expanded", "true")
    expect(await screen.findByText("Desktop runtime manifest verified.")).toBeInTheDocument()
  })

  it("provides a manual recovery state when the desktop bridge is unavailable", async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal("fetch", fetchMock)
    getBootstrapBridgeMock.mockReturnValue(undefined)
    window.history.replaceState({}, "", "/?bootstrap=1")

    render(<App />)

    expect(await screen.findByText("DESKTOP_BRIDGE_UNAVAILABLE")).toBeInTheDocument()
    expect(screen.getByText("desktop-shell")).toBeInTheDocument()
    expect(screen.getByText("桌面环境检查仅在应用内提供。")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "重试" })).not.toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole("button", { name: "打开日志目录" }))
    expect(screen.getByText("桌面日志接口当前不可用，请通过桌面应用入口重新打开。")).toBeInTheDocument()
  })
})
