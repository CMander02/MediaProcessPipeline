import { useState } from "react"
import { HugeiconsIcon } from "@hugeicons/react"
import { ArrowDown01Icon } from "@hugeicons/core-free-icons"

import { openComposer } from "@/lib/composer-store"
import { desktopApp, type DesktopAction } from "@/lib/desktop-bridge"
import { navigate } from "@/lib/router"
import { notifyError } from "@/lib/notify"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuShortcut,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"

/**
 * The MPP menu behind the logo. In the desktop app it also carries what used to be the native
 * menu (Alt): open in a browser, reconnect, project folder, logs and quit.
 */
export function AppMenu() {
  const [owned, setOwned] = useState<boolean | null>(null)

  const run = (action: DesktopAction) => {
    desktopApp?.action(action).catch((error) => notifyError("操作失败", error))
  }

  return (
    <DropdownMenu
      onOpenChange={(open) => {
        if (open && desktopApp) desktopApp.info().then((info) => setOwned(info.owned), () => setOwned(null))
      }}
    >
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className="flex h-8 shrink-0 items-center gap-1.5 rounded-md px-1.5 text-foreground transition-colors hover:bg-muted"
          aria-label="MPP 菜单"
        >
          <img src="/favicon.svg" className="size-5" alt="" aria-hidden="true" />
          <span className="text-sm font-semibold tracking-tight">MPP</span>
          <HugeiconsIcon icon={ArrowDown01Icon} className="size-3.5 text-muted-foreground" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="min-w-56">
        <DropdownMenuItem onClick={() => openComposer()}>
          新建处理
          {desktopApp && <DropdownMenuShortcut>Ctrl+N</DropdownMenuShortcut>}
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => navigate("#/settings")}>设置</DropdownMenuItem>
        <DropdownMenuItem onClick={() => window.location.reload()}>
          刷新页面
          <DropdownMenuShortcut>Ctrl+R</DropdownMenuShortcut>
        </DropdownMenuItem>
        {desktopApp && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={() => run("open-browser")}>在浏览器中打开</DropdownMenuItem>
            <DropdownMenuItem onClick={() => run("reconnect")}>重新连接后端</DropdownMenuItem>
            <DropdownMenuItem onClick={() => run("choose-project")} disabled={owned === true}>
              选择项目目录…
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => run("open-logs")}>打开启动日志目录</DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={() => run("quit")}>
              {owned ? "退出并停止后端" : "退出 MPP 桌面"}
            </DropdownMenuItem>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
