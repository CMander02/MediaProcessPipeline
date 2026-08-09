/** @vitest-environment jsdom */

import { afterEach, describe, expect, it, vi } from "vitest"

import { api, configureApiClient, subscribeAllEvents } from "./api"

afterEach(() => {
  configureApiClient({ baseUrl: "", credentials: "include", credentialProvider: () => ({}), requestedWith: "fetch" })
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

  it("streams Android SSE with the Bearer header", async () => {
    const payload = { task_id: "task-1", type: "snapshot", data: {}, timestamp: "2026-08-09T00:00:00Z" }
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(payload)}\n\n`))
        controller.close()
      },
    })
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(stream, { status: 200 }))
    configureApiClient({
      baseUrl: "https://mpp.example.com",
      credentials: "omit",
      credentialProvider: () => ({ Authorization: "Bearer android-token" }),
      requestedWith: "mpp-android",
    })
    const handler = vi.fn()

    const unsubscribe = subscribeAllEvents(handler)
    await vi.waitFor(() => expect(handler).toHaveBeenCalledWith(payload))
    unsubscribe()

    expect(fetchMock).toHaveBeenCalledWith(
      "https://mpp.example.com/api/tasks/events",
      expect.objectContaining({
        credentials: "omit",
        headers: expect.objectContaining({ Authorization: "Bearer android-token" }),
      }),
    )
  })

  it("marks Android mutations with the native request source", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify([]), { status: 200 }))
    configureApiClient({ requestedWith: "mpp-android" })

    await api.tasks.createBatch(["https://example.com/video"])

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/tasks/batch",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ "X-Requested-With": "mpp-android" }),
      }),
    )
  })
})
