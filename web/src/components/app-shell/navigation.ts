import {
  ComputerTerminal01Icon,
  FolderOpenIcon,
  PlusSignIcon,
} from "@hugeicons/core-free-icons"

import { cn } from "@/lib/utils"

/** Title-bar buttons: the page links and the library filters look the same. */
export function titleBarButtonClass(active: boolean): string {
  return cn(
    "flex h-8 shrink-0 items-center gap-1.5 rounded-md px-2.5 text-[13px] font-medium transition-colors",
    active ? "bg-primary/10 text-primary" : "text-muted-foreground hover:bg-muted hover:text-foreground",
  )
}

export const PRIMARY_NAV_ITEMS = [
  { page: "files", icon: FolderOpenIcon, label: "文件" },
  { page: "submit", icon: PlusSignIcon, label: "处理" },
  { page: "backend", icon: ComputerTerminal01Icon, label: "后端" },
] as const

export type PrimaryPage = (typeof PRIMARY_NAV_ITEMS)[number]["page"]

export const PAGE_TITLES = {
  files: "文件",
  submit: "处理",
  backend: "后端",
  result: "任务结果",
  settings: "设置",
} as const
