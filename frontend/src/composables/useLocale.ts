import { ref, computed, onMounted } from "vue"

export type LocaleCode = "zh" | "en"

const STORAGE_KEY = "pipeline-locale"

// 全局状态
const currentLocale = ref<LocaleCode>("zh")

// 翻译表
const messages: Record<LocaleCode, Record<string, string>> = {
  zh: {
    // 导航
    "nav.dashboard": "首页",
    "nav.tasks": "任务",
    "nav.archives": "归档",
    "nav.settings": "设置",

    // Dashboard
    "dashboard.title": "媒体处理",
    "dashboard.description": "音视频转文字、摘要、思维导图",
    "dashboard.placeholder": "粘贴视频链接或本地文件路径，回车开始处理",
    "dashboard.start": "开始",
    "dashboard.processing": "处理中...",
    "dashboard.youtube": "YouTube 视频",
    "dashboard.bilibili": "Bilibili 视频",
    "dashboard.url": "网络视频",
    "dashboard.local": "本地文件",
    "dashboard.hints": "支持 YouTube、Bilibili、本地音视频文件",
    "dashboard.enterHint": "Enter 快速开始",
    "dashboard.skipSeparation": "跳过人声分离",
    "dashboard.skipDiarization": "跳过说话人识别",
    "dashboard.language": "语言",
    "dashboard.autoDetect": "自动检测",
    "dashboard.totalTasks": "全部任务",
    "dashboard.completed": "已完成",
    "dashboard.processing2": "处理中",
    "dashboard.failed": "失败",
    "dashboard.activeTitle": "正在处理",
    "dashboard.noTasks": "暂无进行中的任务，粘贴链接开始处理",

    // Settings
    "settings.title": "设置",
    "settings.description": "配置处理管线参数",
    "settings.save": "保存设置",
    "settings.saved": "已保存!",
    "settings.appearance": "外观",
    "settings.appearanceDesc": "主题和语言偏好设置",
    "settings.theme": "主题模式",
    "settings.themeLight": "浅色",
    "settings.themeDark": "深色",
    "settings.themeSystem": "跟随系统",
    "settings.language": "界面语言",
    "settings.backendOnline": "后端在线",
    "settings.backendOffline": "后端离线",
    "settings.backendChecking": "检查中",

    // Common
    "common.options": "处理选项",
  },
  en: {
    // Navigation
    "nav.dashboard": "Dashboard",
    "nav.tasks": "Tasks",
    "nav.archives": "Archives",
    "nav.settings": "Settings",

    // Dashboard
    "dashboard.title": "Media Processing",
    "dashboard.description": "Audio/Video to text, summary, mindmap",
    "dashboard.placeholder": "Paste video URL or local file path, press Enter to start",
    "dashboard.start": "Start",
    "dashboard.processing": "Processing...",
    "dashboard.youtube": "YouTube Video",
    "dashboard.bilibili": "Bilibili Video",
    "dashboard.url": "Web Video",
    "dashboard.local": "Local File",
    "dashboard.hints": "Supports YouTube, Bilibili, local audio/video files",
    "dashboard.enterHint": "Press Enter to start",
    "dashboard.skipSeparation": "Skip vocal separation",
    "dashboard.skipDiarization": "Skip speaker diarization",
    "dashboard.language": "Language",
    "dashboard.autoDetect": "Auto Detect",
    "dashboard.totalTasks": "Total Tasks",
    "dashboard.completed": "Completed",
    "dashboard.processing2": "Processing",
    "dashboard.failed": "Failed",
    "dashboard.activeTitle": "Active Tasks",
    "dashboard.noTasks": "No active tasks. Paste a link to start processing.",

    // Settings
    "settings.title": "Settings",
    "settings.description": "Configure pipeline processing options",
    "settings.save": "Save Changes",
    "settings.saved": "Saved!",
    "settings.appearance": "Appearance",
    "settings.appearanceDesc": "Theme and language preferences",
    "settings.theme": "Theme Mode",
    "settings.themeLight": "Light",
    "settings.themeDark": "Dark",
    "settings.themeSystem": "System",
    "settings.language": "Interface Language",
    "settings.backendOnline": "Backend Online",
    "settings.backendOffline": "Backend Offline",
    "settings.backendChecking": "Checking",

    // Common
    "common.options": "Options",
  },
}

export function useLocale() {
  onMounted(() => {
    const stored = localStorage.getItem(STORAGE_KEY) as LocaleCode | null
    if (stored && ["zh", "en"].includes(stored)) {
      currentLocale.value = stored
    }
  })

  const setLocale = (locale: LocaleCode) => {
    currentLocale.value = locale
    localStorage.setItem(STORAGE_KEY, locale)
  }

  const t = (key: string): string => {
    return messages[currentLocale.value][key] || key
  }

  return {
    currentLocale,
    setLocale,
    t,
  }
}
