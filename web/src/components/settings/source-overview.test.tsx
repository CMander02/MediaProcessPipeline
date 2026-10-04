// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { SourceOverview } from "@/components/settings/source-overview"
import type { Settings } from "@/lib/api"

afterEach(cleanup)

const settings = (values: Record<string, unknown>) => values as unknown as Settings

function stateOf(title: string) {
  const tile = screen.getByRole("button", { name: new RegExp(`^${title}`) })
  return tile.textContent?.slice(title.length)
}

describe("source overview", () => {
  it("shows every source's login state and opens the one clicked", () => {
    const onOpen = vi.fn()
    render(
      <SourceOverview
        settings={settings({ youtube_cookies_file: "", youtube_cookies_browser: "firefox" })}
        bilibili={{ logged_in: true, uid: "1", days_left: 3 }}
        twitter={{ storage_state_path: "", storage_state_exists: false, cookie_count: 0, logged_in: false }}
        xiaohongshu={{ configured_cookie: false, storage_state_path: "", storage_state_exists: true, cookie_count: 4, login_cookie: false }}
        onOpen={onOpen}
      />,
    )

    const list = screen.getByRole("list", { name: "来源状态" })
    expect(within(list).getAllByRole("listitem")).toHaveLength(6)
    expect(stateOf("哔哩哔哩")).toBe("3 天后过期")
    expect(stateOf("YouTube")).toBe("已配置 cookies")
    expect(stateOf("X")).toBe("未登录 · 长文需要")
    expect(stateOf("小宇宙")).toBe("无需登录")
    expect(stateOf("小红书")).toBe("已保存会话")
    expect(stateOf("知乎")).toBe("无需登录")

    fireEvent.click(screen.getByRole("button", { name: /^X/ }))
    expect(onOpen).toHaveBeenCalledWith("twitter")
  })

  it("tells an expired or rejected Bilibili login from a missing one", () => {
    const tiles = (bilibili: Parameters<typeof SourceOverview>[0]["bilibili"]) => render(
      <SourceOverview settings={settings({})} bilibili={bilibili} twitter={null} xiaohongshu={null} onOpen={() => {}} />,
    )

    tiles(null)
    expect(stateOf("哔哩哔哩")).toBe("检测中…")
    expect(stateOf("X")).toBe("检测中…")
    expect(stateOf("YouTube")).toBe("未提供 cookies")
    cleanup()

    tiles({ logged_in: false, expires: "2026-09-01T00:00:00+00:00", message: "Cookie 已过期" })
    expect(stateOf("哔哩哔哩")).toBe("已过期")
    cleanup()

    tiles({ logged_in: false, expires: "2026-12-01T00:00:00+00:00", message: "Cookie 无效或未登录" })
    expect(stateOf("哔哩哔哩")).toBe("登录已失效")
    cleanup()

    tiles({ logged_in: false, message: "未配置 Bilibili cookie（settings 或 BBDown.data）" })
    expect(stateOf("哔哩哔哩")).toBe("未登录")
  })
})
