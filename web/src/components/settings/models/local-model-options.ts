

export const LOCAL_SETTINGS_ENTRIES = [
  {
    id: "audio-flow",
    title: "音频流程",
    description: "新任务默认怎么识别，以及是否区分说话人",
  },
  {
    id: "uvr",
    title: "人声分离",
    description: "UVR 本地模型与运行设备",
  },
  {
    id: "sherpa-asr",
    title: "语音识别",
    description: "识别引擎、时间戳与说话人模型",
  },
  {
    id: "local-llm",
    title: "本地大模型",
    description: "在本机运行的文本与图像理解模型",
  },
] as const

export type LocalSettingsId = (typeof LOCAL_SETTINGS_ENTRIES)[number]["id"]
