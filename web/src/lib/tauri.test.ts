/** @vitest-environment jsdom */

import { describe, expect, it } from "vitest"
import {
  normalizeBootstrapPreflight,
  normalizeBootstrapStatus,
  type BackendStatus,
} from "@/lib/tauri"
import scanningPreflight from "@/lib/fixtures/bootstrap-preflight-scanning-v1.json"

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

describe("normalizeBootstrapPreflight", () => {
  const componentIds = [
    "desktop-runtime",
    "data-root",
    "bundled-uv",
    "python-environment",
    "ffmpeg",
    "ffprobe",
    "desktop-proxy-port",
    "backend-private-port",
    "runtime-settings",
    "webview2",
  ]
  const validPreflight = {
    schema_version: 1,
    overall_status: "needs_configuration",
    components: componentIds.map((componentId) => ({
      component_id: componentId,
      label: componentId,
      status: componentId === "runtime-settings" ? "invalid" : "ready",
      required: true,
      version: null,
      path: null,
      error_code: componentId === "runtime-settings" ? "CONFIG_INVALID" : null,
      remediation: componentId === "runtime-settings" ? "Repair the settings file." : null,
      detail: null,
    })),
  }

  it("accepts schema v1 and preserves nullable component fields", () => {
    expect(normalizeBootstrapPreflight(validPreflight)).toEqual(validPreflight)
  })

  it("accepts the native scanning-state contract", () => {
    expect(normalizeBootstrapPreflight(scanningPreflight)).toEqual(scanningPreflight)
  })

  it.each([
    null,
    { ...validPreflight, schema_version: 2 },
    { ...validPreflight, overall_status: "future_status" },
    { ...validPreflight, components: null },
    { ...validPreflight, components: validPreflight.components.slice(0, -1) },
    {
      ...validPreflight,
      components: [...validPreflight.components, validPreflight.components[0]],
    },
    {
      ...validPreflight,
      components: [
        validPreflight.components[1],
        validPreflight.components[0],
        ...validPreflight.components.slice(2),
      ],
    },
    {
      ...validPreflight,
      components: validPreflight.components.map((component, index) => (
        index === 0 ? { ...component, component_id: "future-component" } : component
      )),
    },
    {
      ...validPreflight,
      components: validPreflight.components.map((component, index) => (
        index === 0
          ? {
            ...component,
            status: "future_status",
          }
          : component
      )),
    },
    {
      ...validPreflight,
      components: validPreflight.components.map((component, index) => (
        index === 0 ? { ...component, detail: 42 } : component
      )),
    },
    {
      ...validPreflight,
      components: validPreflight.components.map((component, index) => (
        index === 0 ? { ...component, unexpected: "field" } : component
      )),
    },
    {
      ...validPreflight,
      components: validPreflight.components.map((component, index) => (
        index === 1 ? validPreflight.components[0] : component
      )),
    },
  ])("rejects incompatible or malformed IPC payload %#", (payload) => {
    expect(() => normalizeBootstrapPreflight(payload)).toThrow(
      "Desktop preflight response has an incompatible schema.",
    )
  })
})
