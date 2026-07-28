/** @vitest-environment jsdom */

import { afterEach, describe, expect, it, vi } from "vitest"

import {
  api,
  API_TOKEN_STORAGE_KEY,
  backendUrl,
  DESKTOP_BACKEND_ORIGIN,
  subscribeAllEvents,
} from "./api"

type DesktopWindow = Window & { isTauri?: boolean }

afterEach(() => {
  delete (window as DesktopWindow).isTauri
  window.localStorage.clear()
  vi.unstubAllGlobals()
})

describe("backendUrl", () => {
  it("keeps browser requests relative for the FastAPI and Vite origins", () => {
    expect(backendUrl("/api/tasks")).toBe("/api/tasks")
  })

  it("routes packaged desktop requests to the authenticated native proxy", () => {
    ;(window as DesktopWindow).isTauri = true

    expect(DESKTOP_BACKEND_ORIGIN).toBe("http://api.tauri.localhost:18000")
    expect(backendUrl("/api/tasks")).toBe(`${DESKTOP_BACKEND_ORIGIN}/api/tasks`)
    expect(() => backendUrl("https://attacker.invalid/api")).toThrow(
      "Backend path must be root-relative",
    )
  })

  it("uses the proxy cookie without exposing the configured API token", async () => {
    ;(window as DesktopWindow).isTauri = true
    window.localStorage.setItem(API_TOKEN_STORAGE_KEY, "private-api-token")
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      text: async () => JSON.stringify({ workers: [] }),
    })
    vi.stubGlobal("fetch", fetchMock)

    await api.workers.list()

    expect(fetchMock).toHaveBeenCalledOnce()
    const [url, options] = fetchMock.mock.calls[0]
    expect(url).toBe(`${DESKTOP_BACKEND_ORIGIN}/api/workers`)
    expect(options.credentials).toBe("include")
    expect(options.headers.Authorization).toBeUndefined()
  })

  it("includes proxy credentials for desktop SSE subscriptions", () => {
    ;(window as DesktopWindow).isTauri = true
    const created: Array<{ url: string; withCredentials: boolean }> = []
    class FakeEventSource {
      onmessage: ((event: MessageEvent<string>) => void) | null = null

      constructor(url: string | URL, options?: EventSourceInit) {
        created.push({
          url: String(url),
          withCredentials: options?.withCredentials ?? false,
        })
      }

      close() {}
    }
    vi.stubGlobal("EventSource", FakeEventSource)

    const unsubscribe = subscribeAllEvents(() => {})
    unsubscribe()

    expect(created).toEqual([
      {
        url: `${DESKTOP_BACKEND_ORIGIN}/api/tasks/events`,
        withCredentials: true,
      },
    ])
  })
})
