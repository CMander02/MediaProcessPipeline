/** @vitest-environment jsdom */

import { afterEach, describe, expect, it, vi } from "vitest"

import { api, configureApiClient } from "./api"

afterEach(() => {
  configureApiClient({ baseUrl: "", credentials: "include", credentialProvider: () => ({}) })
  vi.restoreAllMocks()
})

describe("API client", () => {
  it("supports a configured base URL and credential provider", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      required: false,
      authenticated: true,
      mode: "local",
    }), { status: 200 }))
    configureApiClient({
      baseUrl: "https://mpp.example.com/",
      credentialProvider: () => ({ Authorization: "Bearer cli-token" }),
    })

    await api.auth.status()

    expect(fetchMock).toHaveBeenCalledWith(
      "https://mpp.example.com/api/auth/status",
      expect.objectContaining({
        credentials: "include",
        headers: expect.objectContaining({ Authorization: "Bearer cli-token" }),
      }),
    )
  })

  it("reports authorization errors through a shared browser event", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(
      JSON.stringify({ detail: "需要访问令牌" }),
      { status: 401, statusText: "Unauthorized" },
    ))
    const handler = vi.fn()
    window.addEventListener("mpp:api-error", handler)

    await expect(api.tasks.stats()).rejects.toMatchObject({ status: 401 })
    expect(handler).toHaveBeenCalledOnce()

    window.removeEventListener("mpp:api-error", handler)
  })

  it("reports network failures as offline errors", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"))
    const handler = vi.fn()
    window.addEventListener("mpp:offline", handler)

    await expect(api.health()).rejects.toMatchObject({ status: 0 })
    expect(handler).toHaveBeenCalledOnce()

    window.removeEventListener("mpp:offline", handler)
  })
})
