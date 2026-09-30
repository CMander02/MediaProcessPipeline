// @vitest-environment jsdom
import { act, cleanup, render } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { GlobalIntake } from "@/components/composer/global-intake"

const mocks = vi.hoisted(() => ({ lookup: vi.fn(), readText: vi.fn(), toast: vi.fn() }))
vi.mock("sonner", () => ({ toast: mocks.toast }))
vi.mock("@/lib/api", () => ({ api: { archives: { lookup: mocks.lookup } } }))
vi.mock("@/hooks/use-preferences", () => ({ getPreferences: () => ({ clipboardDetect: true }) }))
vi.mock("@/hooks/use-app-access-context", () => ({
  useAppAccess: () => ({
    online: true,
    capabilities: { url_submission: true, browser_file_upload: true, local_path_submission: false },
  }),
}))

const BILIBILI = "https://www.bilibili.com/video/BV1Ab4y1C7dE"
const YOUTUBE = "https://www.youtube.com/watch?v=aBcDeFgHiJk"

/** Switch back to MPP with this on the clipboard and let the check finish. */
async function returnWith(clipboard: string) {
  mocks.readText.mockResolvedValue(clipboard)
  await act(async () => {
    window.dispatchEvent(new Event("focus"))
    await new Promise((resolve) => setTimeout(resolve, 0))
  })
}

function memoryStorage(): Storage {
  const values = new Map<string, string>()
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => { values.set(key, value) },
    removeItem: (key) => { values.delete(key) },
    clear: () => values.clear(),
    key: () => null,
    get length() { return values.size },
  }
}

beforeEach(() => {
  vi.stubGlobal("localStorage", memoryStorage())
  mocks.toast.mockClear()
  mocks.lookup.mockReset().mockResolvedValue({ matches: {} })
  Object.defineProperty(navigator, "clipboard", { value: { readText: mocks.readText }, configurable: true })
  vi.spyOn(document, "hasFocus").mockReturnValue(true)
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe("clipboard prompt", () => {
  it("offers a link once, even when copied again or after a restart", async () => {
    render(<GlobalIntake />)
    await returnWith(BILIBILI)
    await returnWith(BILIBILI)
    await returnWith(`【分享】 ${BILIBILI}?spm_id_from=333.1007&vd_source=abc 复制打开`)
    cleanup()
    render(<GlobalIntake />)
    await returnWith(BILIBILI)

    expect(mocks.toast).toHaveBeenCalledTimes(1)
    expect(mocks.toast.mock.calls[0][0]).toBe("剪贴板里有一个链接")
  })

  it("offers only the links that are new on the clipboard", async () => {
    render(<GlobalIntake />)
    await returnWith(BILIBILI)
    await returnWith(`${BILIBILI}\n${YOUTUBE}`)

    expect(mocks.toast).toHaveBeenCalledTimes(2)
    expect(mocks.toast.mock.calls[1][0]).toBe("剪贴板里有一个链接")
    expect(mocks.lookup).toHaveBeenLastCalledWith([YOUTUBE])
  })

  it("stays quiet about links already in the library", async () => {
    mocks.lookup.mockResolvedValue({ matches: { [YOUTUBE]: [{ path: "D:/lib/a", status: "completed", processing: false }] } })
    render(<GlobalIntake />)
    await returnWith(YOUTUBE)

    expect(mocks.lookup).toHaveBeenCalledWith([YOUTUBE])
    expect(mocks.toast).not.toHaveBeenCalled()
  })
})
