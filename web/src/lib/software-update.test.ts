/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from "vitest"
import { api, configureApiClient } from "./api"

const release = {
  current_version: "0.9.0", latest_version: "0.10.0", update_available: true,
  release_url: "https://github.com/CMander02/MediaProcessPipeline/releases/tag/v0.10.0",
  published_at: null, notes: "更新说明", assets: [],
}

afterEach(() => {
  configureApiClient({ baseUrl: "", credentials: "include", credentialProvider: () => ({}), requestedWith: "fetch" })
  vi.restoreAllMocks()
})

describe("update API boundary", () => {
  it("checks a cached release by default and supports force", async () => {
    const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify(release)))
    expect(await api.updates.check()).toEqual(release)
    await api.updates.check(true)
    expect(fetch.mock.calls.map(([url]) => url)).toEqual(["/api/system/update/check?force=false", "/api/system/update/check?force=true"])
  })

  it("downloads with authentication and serializes the optional asset selection", async () => {
    const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify({ path: "/tmp/update.dmg", asset_name: "update.dmg", sha256: "a".repeat(64), version: "0.10.0", instructions: "打开安装包。" })))
    configureApiClient({ credentialProvider: () => ({ Authorization: "Bearer token" }) })
    await api.updates.download("update.dmg")
    await api.updates.download()
    expect(fetch.mock.calls.map(([, options]) => JSON.parse(String(options?.body)))).toEqual([{ asset_name: "update.dmg" }, {}])
    expect(fetch).toHaveBeenCalledWith("/api/system/update/download", expect.objectContaining({ method: "POST", headers: expect.objectContaining({ Authorization: "Bearer token", "X-Requested-With": "fetch" }) }))
  })

  it("rejects malformed releases and unsafe release URLs", async () => {
    const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify({ ...release, release_url: "javascript:alert(1)" })))
    await expect(api.updates.check()).rejects.toThrow("更新检查响应格式无效")
    fetch.mockImplementation(async () => new Response(JSON.stringify({ ...release, assets: [{ name: "asset" }] })))
    await expect(api.updates.check()).rejects.toThrow("更新检查响应格式无效")
  })

  it("rejects downloads missing checksum verification data and preserves server errors", async () => {
    const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify({ path: "/tmp/update" })))
    await expect(api.updates.download()).rejects.toThrow("更新下载响应格式无效")
    fetch.mockImplementation(async () => new Response(JSON.stringify({ detail: "SHA-256 校验失败" }), { status: 502 }))
    await expect(api.updates.download()).rejects.toThrow("SHA-256 校验失败")
  })
})
