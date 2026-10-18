export interface WorkflowConfig {
  source_analysis: boolean
  separate: boolean
  diarize: boolean
  speaker_review: boolean
  polish: boolean
  analyze: boolean
  summary: boolean
  mindmap: boolean
  detail: boolean
}

export const DEFAULT_WORKFLOW: WorkflowConfig = {
  source_analysis: true, separate: true, diarize: true, speaker_review: true,
  polish: true, analyze: true, summary: true, mindmap: true, detail: true,
}

export function workflowDefaults(settings: {
  pipeline_workflow?: Partial<WorkflowConfig> | null
  generate_video_detail?: boolean
  enable_diarization?: boolean
}): WorkflowConfig {
  return { ...DEFAULT_WORKFLOW, detail: settings.generate_video_detail ?? true,
    diarize: settings.enable_diarization ?? true, ...settings.pipeline_workflow }
}

export const WORKFLOW_GROUPS: { label: string; steps: { key: keyof WorkflowConfig; label: string; description: string }[] }[] = [
  { label: "识别前", steps: [
    { key: "source_analysis", label: "素材分析", description: "分析标题、简介和画面，补充术语与人物线索。" },
    { key: "separate", label: "人声分离", description: "去除背景音乐。无 GPU 的服务器耗时较长。" },
  ] },
  { label: "识别后", steps: [
    { key: "diarize", label: "区分说话人", description: "按声音区分说话人；声纹功能按已有设置运行。" },
    { key: "speaker_review", label: "说话人修正", description: "用模型核对说话人标签和姓名。" },
    { key: "polish", label: "字幕润色", description: "修正用词与断句，保留原始字幕。" },
    { key: "analyze", label: "内容分析", description: "提取主题、关键词和内容类型。" },
    { key: "summary", label: "摘要", description: "生成总结与要点。" },
    { key: "mindmap", label: "思维导图", description: "生成可浏览的内容结构。" },
    { key: "detail", label: "视频详情", description: "额外生成详细的 Markdown 笔记。" },
  ] },
]
