/** @vitest-environment jsdom */

import { describe, expect, it } from "vitest"
import { normalizeBootstrapStatus, type BackendStatus } from "@/lib/tauri"

function backendStatus(overrides: Partial<BackendStatus> = {}): BackendStatus {
  return {
    state: "stopped",
    command: "mpp-runtime run",
    cwd: "backend",
    pid: null,
    url: "http://localhost:18000",
    message: "Ready.",
    ...overrides,
  }
}

describe("normalizeBootstrapStatus", () => {
  it("preserves native bootstrap fields", () => {
    const status = normalizeBootstrapStatus(backendStatus({
      state: "error",
      phase: "FAILED_MANUAL",
      error_code: "RUNTIME_INVALID",
      component_id: "desktop-runtime",
      remediation: "Repair the installation.",
      local_path: "C:\\Program Files\\MPP\\runtime",
    }))

    expect(status).toMatchObject({
      phase: "FAILED_MANUAL",
      error_code: "RUNTIME_INVALID",
      component_id: "desktop-runtime",
      remediation: "Repair the installation.",
      local_path: "C:\\Program Files\\MPP\\runtime",
    })
  })

  it("maps legacy backend states to stable bootstrap phases", () => {
    expect(normalizeBootstrapStatus(backendStatus()).phase).toBe("READY_TO_START")
    expect(normalizeBootstrapStatus(backendStatus({
      state: "starting",
      message: "Waiting for the health endpoint.",
    })).phase).toBe("WAITING_HEALTH")
    expect(normalizeBootstrapStatus(backendStatus({
      state: "starting",
      message: "Spawning process.",
    })).phase).toBe("STARTING_BACKEND")
    expect(normalizeBootstrapStatus(backendStatus({ state: "running" })).phase).toBe("APP_READY")
    expect(normalizeBootstrapStatus(backendStatus({ state: "error" })).phase).toBe("FAILED_RETRYABLE")
  })
})
