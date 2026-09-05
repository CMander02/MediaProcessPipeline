

export const LOCAL_SETTINGS_ENTRIES = [
  {
    id: "audio-flow",
    title: "音频流程",
    description: "默认处理方式与说话人标签",
  },
  {
    id: "uvr",
    title: "人声分离",
    description: "UVR 本地模型与运行设备",
  },
  {
    id: "sherpa-asr",
    title: "ASR",
    description: "语音识别、时间戳与说话人模型",
  },
  {
    id: "local-llm",
    title: "LLM",
    description: "本地文本与图像理解",
  },
] as const

export type LocalSettingsId = (typeof LOCAL_SETTINGS_ENTRIES)[number]["id"]
