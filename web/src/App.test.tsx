/** @vitest-environment jsdom */
import type { ReactNode } from "react"
import { act, cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import App from "./App"
import type { LibraryControls } from "@/hooks/use-library-controls"
import { navigate, type Route } from "@/lib/router"

vi.mock("@/hooks/use-app-access", () => ({ AppAccessBoundary: ({ children }: { children: ReactNode }) => children }))
vi.mock("@/hooks/use-app-startup", () => ({ useAppStartup: vi.fn() }))
vi.mock("@/hooks/use-task-notifications", () => ({ useTaskNotifications: vi.fn() }))
vi.mock("@/components/app-shell/app-shell", () => ({
  AppShell: ({ activePage, toolbar, crumbs, trailing, children }: { activePage: Route["page"]; toolbar?: ReactNode; crumbs?: ReactNode; trailing?: ReactNode; children: ReactNode }) => <>
    <header data-active-page={activePage}>{crumbs}{toolbar}{trailing}</header>
    {children}
  </>,
}))
vi.mock("@/components/app-shell/page-toolbar", () => ({
  PageToolbar: ({ library }: { library: LibraryControls }) => <div role="search" aria-label="文件搜索与筛选">{library.search}</div>,
}))
vi.mock("@/components/library/library-navigation", () => ({
  LibraryCrumbs: () => <nav aria-label="文件位置" />,
  LibraryViewControls: () => <div role="group" aria-label="文件视图" />,
}))
vi.mock("@/components/app-outlet", () => ({ AppOutlet: ({ route }: { route: Route }) => <main>{route.page}</main> }))
vi.mock("@/components/composer/composer-dialog", () => ({ ComposerDialog: () => null }))
vi.mock("@/components/composer/global-intake", () => ({ GlobalIntake: () => null }))
vi.mock("@/components/search/command-search", () => ({ CommandSearch: () => null }))
vi.mock("@/components/pwa-status", () => ({ PwaStatus: () => null }))
vi.mock("@/components/ui/sonner", () => ({ Toaster: () => null }))

afterEach(cleanup)

describe("shared library header", () => {
  it.each(["#/result/archive?path=D%3A%5Carchive&taskId=task-1", "#/result/task/task-1"])("keeps the complete library header on %s", (resultHash) => {
    act(() => navigate("#/files?q=course-search&page=3&folder=course"))
    render(<App />)

    const search = screen.getByRole("search", { name: "文件搜索与筛选" })
    act(() => navigate(resultHash))

    expect(screen.getByRole("main").textContent).toBe("result")
    expect(screen.getByRole("banner").getAttribute("data-active-page")).toBe("files")
    expect(screen.getByRole("navigation", { name: "文件位置" })).toBeTruthy()
    expect(screen.getByRole("group", { name: "文件视图" })).toBeTruthy()
    expect(screen.getByRole("search", { name: "文件搜索与筛选" })).toBe(search)
    expect(search.textContent).toBe("course-search")

    act(() => navigate("#/settings"))
    expect(screen.getByRole("banner").getAttribute("data-active-page")).toBe("settings")
    expect(screen.queryByRole("search", { name: "文件搜索与筛选" })).toBeNull()
  })
})
