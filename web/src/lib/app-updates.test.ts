// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest"

type Updates = typeof import("./app-updates")

let reload: ReturnType<typeof vi.fn>

async function load(): Promise<Updates> {
  vi.resetModules()
  return import("./app-updates")
}

function setVisibility(state: "visible" | "hidden", notify = true) {
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state })
  if (notify) document.dispatchEvent(new Event("visibilitychange"))
}

beforeEach(() => {
  reload = vi.fn()
  Object.defineProperty(window, "location", { configurable: true, value: { ...window.location, reload } })
  sessionStorage.clear()
  document.body.innerHTML = ""
  setVisibility("visible", false)
})

describe("loading a new build", () => {
  it("reloads at once while the window is in the background, and says so afterwards", async () => {
    setVisibility("hidden", false)
    const updates = await load()
    updates.scheduleUpdateReload()
    expect(reload).toHaveBeenCalledTimes(1)
    expect(updates.consumeUpdatedFlag()).toBe(true)
    expect(updates.consumeUpdatedFlag()).toBe(false)
  })

  it("waits for the next page change while the window is in front", async () => {
    const updates = await load()
    updates.scheduleUpdateReload()
    expect(reload).not.toHaveBeenCalled()
    window.dispatchEvent(new HashChangeEvent("hashchange"))
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it("does not cut off playback when the window goes to the background", async () => {
    const audio = document.createElement("audio")
    let paused = false
    Object.defineProperty(audio, "paused", { get: () => paused })
    document.body.append(audio)
    const updates = await load()
    updates.scheduleUpdateReload()
    setVisibility("hidden")
    expect(reload).not.toHaveBeenCalled()
    paused = true
    setVisibility("visible")
    setVisibility("hidden")
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it("reloads once for a missing chunk of the old build, not in a loop", async () => {
    const updates = await load()
    expect(updates.reloadAfterMissingChunk()).toBe(true)
    expect(updates.reloadAfterMissingChunk()).toBe(false)
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it("finds the entry script in index.html", async () => {
    const updates = await load()
    const html = '<script type="module" crossorigin src="/assets/index-CGE2j0fF.js"></script>'
    expect(updates.entryScript(html)).toBe("/assets/index-CGE2j0fF.js")
    expect(updates.entryScript("<html></html>")).toBeNull()
  })

  it("checks for a new build when the window comes back to the front", async () => {
    const updates = await load()
    const check = vi.fn().mockResolvedValue(true)
    const stop = updates.watchForNewBuild(check)
    window.dispatchEvent(new Event("focus"))
    await vi.waitFor(() => expect(check).toHaveBeenCalledTimes(1))
    window.dispatchEvent(new HashChangeEvent("hashchange"))
    expect(reload).toHaveBeenCalledTimes(1)
    stop()
  })
})

describe("the desktop app without a service worker", () => {
  it("removes an old service worker and its cache, then reloads from the daemon", async () => {
    const unregister = vi.fn().mockResolvedValue(true)
    Object.defineProperty(navigator, "serviceWorker", {
      configurable: true,
      value: { getRegistrations: vi.fn().mockResolvedValue([{ unregister }]), controller: {} },
    })
    const remove = vi.fn().mockResolvedValue(true)
    Object.defineProperty(window, "caches", {
      configurable: true,
      value: { keys: vi.fn().mockResolvedValue(["workbox-precache-v2-http://localhost:18000/", "other"]), delete: remove },
    })
    const updates = await load()

    await updates.removeServiceWorkers()

    expect(unregister).toHaveBeenCalled()
    expect(remove).toHaveBeenCalledWith("workbox-precache-v2-http://localhost:18000/")
    expect(remove).not.toHaveBeenCalledWith("other")
    expect(reload).toHaveBeenCalledTimes(1)
  })
})
