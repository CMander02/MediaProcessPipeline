import {
  ComputerTerminal01Icon,
  FolderOpenIcon,
  PlusSignIcon,
} from "@hugeicons/core-free-icons"

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
