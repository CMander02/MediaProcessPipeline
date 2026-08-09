import { describe, expect, it } from "vitest"

import { normalizeServerUrl } from "@/platform/server-url"

describe("Android server URL", () => {
  it.each([
    ["https://mpp.example.com/", "https://mpp.example.com"],
    ["http://localhost:18000", "http://localhost:18000"],
    ["http://192.168.1.20:18000", "http://192.168.1.20:18000"],
    ["http://172.16.2.4:18000", "http://172.16.2.4:18000"],
  ])("accepts trusted server address %s", (input, expected) => {
    expect(normalizeServerUrl(input)).toBe(expected)
  })

  it.each([
    "mpp.example.com",
    "http://mpp.example.com",
    "https://mpp.example.com/api",
    "https://token@mpp.example.com",
  ])("rejects unsafe or ambiguous address %s", (input) => {
    expect(() => normalizeServerUrl(input)).toThrow()
  })
})
