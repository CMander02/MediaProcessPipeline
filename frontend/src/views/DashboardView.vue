<script setup lang="ts">
import { ref, computed, watch } from "vue"
import {
  ElButton,
  ElInput,
  ElSelect,
  ElOption,
  ElSwitch,
  ElTag,
  ElProgress,
  ElIcon,
  ElTooltip,
} from "element-plus"
import {
  VideoPlay,
  Link,
  FolderOpened,
  Loading,
  CircleCheck,
  CircleClose,
  Clock,
  DataLine,
  Setting,
  Close,
  Paperclip,
} from "@element-plus/icons-vue"
import { useTasks } from "@/composables/useTasks"
import type { PipelineOptions, Task } from "@/types"

const { tasks, createTask } = useTasks()

const source = ref("")
const submitting = ref(false)
const showOptions = ref(false)
const fileInputRef = ref<HTMLInputElement | null>(null)
const selectedFile = ref<File | null>(null)

// Handle native file selection
const handleFileChange = (e: Event) => {
  const input = e.target as HTMLInputElement
  if (input.files && input.files.length > 0) {
    const file = input.files[0]
    selectedFile.value = file
    source.value = `[本地文件] ${file.name}`
  }
  // 重置 input 以便可以再次选择同一文件
  input.value = ""
}

const openFilePicker = () => {
  fileInputRef.value?.click()
}
const options = ref<PipelineOptions>({
  skip_download: false,
  skip_separation: false,
  skip_diarization: false,
  language: "auto",
})

// 智能检测输入类型
const inputType = computed(() => {
  const val = source.value.trim()
  if (!val) return null
  if (val.startsWith("http://") || val.startsWith("https://")) {
    // 识别常见视频平台
    if (val.includes("youtube.com") || val.includes("youtu.be")) return "youtube"
    if (val.includes("bilibili.com") || val.includes("b23.tv")) return "bilibili"
    return "url"
  }
  // 本地路径
  if (val.match(/^[a-zA-Z]:\\/) || val.startsWith("/") || val.startsWith("./")) {
    return "local"
  }
  return null
})

const inputIcon = computed(() => {
  if (!inputType.value) return null
  return inputType.value === "local" ? FolderOpened : Link
})

const placeholderText = "粘贴视频链接或本地文件路径，回车开始处理"

// 输入提示
const inputHint = computed(() => {
  const val = source.value.trim()
  if (!val) return null
  switch (inputType.value) {
    case "youtube":
      return "YouTube 视频"
    case "bilibili":
      return "Bilibili 视频"
    case "url":
      return "网络视频"
    case "local":
      return "本地文件"
    default:
      return null
  }
})

const handleSubmit = async () => {
  if (!source.value.trim() || submitting.value) return

  submitting.value = true
  try {
    let taskSource = source.value.trim()

    // 如果有选中的本地文件，先上传
    if (selectedFile.value) {
      const formData = new FormData()
      formData.append("file", selectedFile.value)

      const uploadRes = await fetch("http://localhost:8000/api/pipeline/upload", {
        method: "POST",
        body: formData,
      })

      if (!uploadRes.ok) {
        throw new Error("文件上传失败")
      }

      const uploadData = await uploadRes.json()
      taskSource = uploadData.file_path
    }

    await createTask({
      task_type: "pipeline",
      source: taskSource,
      options: { ...options.value },
    })
    source.value = ""
    selectedFile.value = null
    showOptions.value = false
  } catch (error) {
    console.error("Failed to create task:", error)
  } finally {
    submitting.value = false
  }
}

// 键盘快捷键
const handleKeydown = (e: KeyboardEvent) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault()
    handleSubmit()
  }
}

// Stats
const completed = computed(() => tasks.value.filter((t) => t.status === "completed").length)
const processing = computed(() =>
  tasks.value.filter((t) => t.status === "processing" || t.status === "queued").length
)
const failed = computed(() => tasks.value.filter((t) => t.status === "failed").length)

// Active tasks
const activeTasks = computed(() =>
  tasks.value
    .filter((t) => t.status === "queued" || t.status === "processing")
    .slice(0, 5)
)

const getProgress = (task: Task) => Math.round(task.progress * 100)

// Pipeline step definitions
const pipelineSteps = [
  { id: "download", name: "下载媒体" },
  { id: "separate", name: "分离人声" },
  { id: "transcribe", name: "转录音频" },
  { id: "analyze", name: "分析内容" },
  { id: "polish", name: "润色字幕" },
  { id: "summarize", name: "生成摘要" },
  { id: "archive", name: "归档保存" },
]

const getStepStatus = (task: Task, stepId: string): "completed" | "active" | "pending" => {
  if (task.completed_steps?.includes(stepId)) return "completed"
  if (task.current_step === stepId) return "active"
  return "pending"
}

const languageOptions = [
  { value: "auto", label: "自动检测" },
  { value: "zh", label: "中文" },
  { value: "en", label: "英语" },
  { value: "ja", label: "日语" },
  { value: "ko", label: "韩语" },
  { value: "de", label: "德语" },
  { value: "fr", label: "法语" },
  { value: "es", label: "西班牙语" },
]
</script>

<template>
  <div class="page-container">
    <!-- 简洁的页面标题 -->
    <div class="page-header">
      <h1 class="page-title">媒体处理</h1>
      <p class="page-description">音视频转文字、摘要、思维导图</p>
    </div>

    <!-- 核心输入区域 - 大而醒目 -->
    <div class="input-section">
      <div class="input-wrapper" :class="{ 'has-content': source.trim(), 'is-submitting': submitting }">
        <div class="input-container">
          <el-icon v-if="inputIcon" class="input-type-icon">
            <component :is="inputIcon" />
          </el-icon>
          <input
            v-model="source"
            type="text"
            :placeholder="placeholderText"
            class="main-input"
            :disabled="submitting"
            @keydown="handleKeydown"
          />
          <transition name="fade">
            <span v-if="inputHint" class="input-hint-badge">{{ inputHint }}</span>
          </transition>
          <input
            ref="fileInputRef"
            type="file"
            accept="video/*,audio/*,.mp4,.mkv,.avi,.webm,.mov,.mp3,.wav,.flac,.m4a,.ogg"
            class="hidden-file-input"
            @change="handleFileChange"
          />
          <el-tooltip content="选择本地文件" placement="top">
            <button
              type="button"
              class="file-picker-toggle"
              @click="openFilePicker"
            >
              <el-icon><Paperclip /></el-icon>
            </button>
          </el-tooltip>
          <el-tooltip content="处理选项" placement="top">
            <button
              type="button"
              class="options-toggle"
              :class="{ active: showOptions }"
              @click="showOptions = !showOptions"
            >
              <el-icon><Setting /></el-icon>
            </button>
          </el-tooltip>
          <button
            type="button"
            class="submit-button"
            :disabled="!source.trim() || submitting"
            @click="handleSubmit"
          >
            <el-icon v-if="submitting" class="spin"><Loading /></el-icon>
            <el-icon v-else><VideoPlay /></el-icon>
            <span>{{ submitting ? "处理中..." : "开始" }}</span>
          </button>
        </div>

        <!-- 展开的选项面板 -->
        <transition name="slide">
          <div v-if="showOptions" class="options-panel">
            <div class="options-grid">
              <div class="option-item">
                <span class="option-label">跳过人声分离</span>
                <el-switch v-model="options.skip_separation" size="small" />
              </div>
              <div class="option-item">
                <span class="option-label">跳过说话人识别</span>
                <el-switch v-model="options.skip_diarization" size="small" />
              </div>
              <div class="option-item language-item">
                <span class="option-label">语言</span>
                <el-select v-model="options.language" size="small" class="language-select">
                  <el-option
                    v-for="opt in languageOptions"
                    :key="opt.value"
                    :value="opt.value"
                    :label="opt.label"
                  />
                </el-select>
              </div>
            </div>
          </div>
        </transition>
      </div>

      <!-- 快捷提示 -->
      <div class="quick-hints">
        <span class="hint-item">支持 YouTube、Bilibili、本地音视频文件</span>
        <span class="hint-sep">·</span>
        <span class="hint-item">Enter 快速开始</span>
      </div>
    </div>

    <!-- 统计卡片 -->
    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-card-icon total">
          <el-icon><DataLine /></el-icon>
        </div>
        <div class="stat-card-content">
          <div class="stat-card-value">{{ tasks.length }}</div>
          <div class="stat-card-label">全部任务</div>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-card-icon success">
          <el-icon><CircleCheck /></el-icon>
        </div>
        <div class="stat-card-content">
          <div class="stat-card-value">{{ completed }}</div>
          <div class="stat-card-label">已完成</div>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-card-icon processing">
          <el-icon :class="{ 'spin': processing > 0 }"><Clock /></el-icon>
        </div>
        <div class="stat-card-content">
          <div class="stat-card-value">{{ processing }}</div>
          <div class="stat-card-label">处理中</div>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-card-icon error">
          <el-icon><CircleClose /></el-icon>
        </div>
        <div class="stat-card-content">
          <div class="stat-card-value">{{ failed }}</div>
          <div class="stat-card-label">失败</div>
        </div>
      </div>
    </div>

    <!-- 活跃任务 - 步骤式进度 -->
    <div v-if="activeTasks.length > 0" class="active-section">
      <h2 class="section-title">正在处理</h2>
      <div class="active-tasks-list">
        <div
          v-for="task in activeTasks"
          :key="task.id"
          class="active-task-item"
        >
          <div class="task-header">
            <div class="task-info">
              <el-icon class="task-spinner spin"><Loading /></el-icon>
              <span class="task-source">{{ task.source }}</span>
            </div>
          </div>
          <!-- Step-based progress -->
          <div class="task-steps">
            <div
              v-for="step in pipelineSteps"
              :key="step.id"
              class="step-item"
              :class="getStepStatus(task, step.id)"
            >
              <span class="step-icon">
                <el-icon v-if="getStepStatus(task, step.id) === 'completed'"><CircleCheck /></el-icon>
                <el-icon v-else-if="getStepStatus(task, step.id) === 'active'" class="spin"><Loading /></el-icon>
                <span v-else class="step-dot" />
              </span>
              <span class="step-name">{{ step.name }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 空状态 -->
    <div v-else class="empty-state-inline">
      <el-icon class="empty-icon"><Clock /></el-icon>
      <span>暂无进行中的任务，粘贴链接开始处理</span>
    </div>

  </div>
</template>

<style scoped>
/* 输入区域 - 核心交互 */
.input-section {
  margin-bottom: 32px;
}

.input-wrapper {
  background: var(--bg-elevated);
  border: 2px solid var(--border-color);
  border-radius: 16px;
  transition: all 0.2s ease;
  overflow: hidden;
}

.input-wrapper:focus-within,
.input-wrapper.has-content {
  border-color: var(--primary-color);
  box-shadow: 0 0 0 4px var(--primary-bg);
}

.input-wrapper.is-submitting {
  opacity: 0.7;
  pointer-events: none;
}

.input-container {
  display: flex;
  align-items: center;
  padding: 8px 8px 8px 20px;
  gap: 12px;
}

.input-type-icon {
  font-size: 20px;
  color: var(--primary-color);
  flex-shrink: 0;
}

.main-input {
  flex: 1;
  border: none;
  outline: none;
  background: transparent;
  font-size: 16px;
  color: var(--text-primary);
  padding: 12px 0;
}

.main-input::placeholder {
  color: var(--text-muted);
}

.input-hint-badge {
  padding: 4px 10px;
  background: var(--primary-bg);
  color: var(--primary-color);
  border-radius: 12px;
  font-size: 12px;
  font-weight: 500;
  white-space: nowrap;
}

.hidden-file-input {
  display: none;
}

.file-picker-toggle,
.options-toggle {
  width: 40px;
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  background: var(--bg-base);
  border-radius: 10px;
  cursor: pointer;
  color: var(--text-muted);
  transition: all 0.15s ease;
  flex-shrink: 0;
}

.file-picker-toggle:hover,
.options-toggle:hover {
  background: var(--border-color);
  color: var(--text-primary);
}

.options-toggle.active {
  background: var(--primary-bg);
  color: var(--primary-color);
}

.submit-button {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 24px;
  background: var(--primary-color);
  color: #fff;
  border: none;
  border-radius: 10px;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
  flex-shrink: 0;
}

.submit-button:hover:not(:disabled) {
  background: var(--primary-dark);
}

.submit-button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* 选项面板 */
.options-panel {
  padding: 16px 20px;
  border-top: 1px solid var(--border-color);
  background: var(--bg-base);
}

.options-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 24px;
}

.option-item {
  display: flex;
  align-items: center;
  gap: 12px;
}

.option-label {
  font-size: 14px;
  color: var(--text-secondary);
}

.language-item {
  margin-left: auto;
}

.language-select {
  width: 120px;
}

/* 快捷提示 */
.quick-hints {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  margin-top: 12px;
  font-size: 13px;
  color: var(--text-muted);
}

.hint-sep {
  color: var(--border-color);
}

/* 活跃任务区域 */
.active-section {
  margin-top: 32px;
}

.section-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 16px;
}

.active-tasks-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.active-task-item {
  padding: 16px 20px;
  background: var(--bg-elevated);
  border: 1px solid var(--border-color);
  border-radius: var(--border-radius);
}

.task-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.task-info {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  flex: 1;
}

.task-spinner {
  color: var(--primary-color);
  flex-shrink: 0;
}

.task-source {
  font-weight: 500;
  font-size: 14px;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.task-percent {
  font-size: 14px;
  font-weight: 600;
  color: var(--primary-color);
}

/* Step-based progress */
.task-steps {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
  margin-top: 12px;
}

.step-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--text-muted);
}

.step-item.completed {
  color: var(--success-color, #67c23a);
}

.step-item.active {
  color: var(--primary-color);
  font-weight: 500;
}

.step-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
}

.step-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--border-color);
}

.step-item.active .step-dot {
  background: var(--primary-color);
}

.step-name {
  white-space: nowrap;
}

/* 空状态（简化版） */
.empty-state-inline {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 40px 20px;
  color: var(--text-muted);
  font-size: 14px;
}

.empty-icon {
  font-size: 18px;
}

/* 动画 */
.spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.15s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

.slide-enter-active,
.slide-leave-active {
  transition: all 0.2s ease;
}

.slide-enter-from,
.slide-leave-to {
  opacity: 0;
  transform: translateY(-10px);
}

/* 响应式 */
@media (max-width: 768px) {
  .input-container {
    flex-wrap: wrap;
    padding: 12px;
  }

  .main-input {
    width: 100%;
    order: 1;
  }

  .input-type-icon {
    order: 0;
  }

  .input-hint-badge {
    order: 2;
  }

  .options-toggle,
  .submit-button {
    order: 3;
  }

  .options-grid {
    flex-direction: column;
    gap: 16px;
  }

  .language-item {
    margin-left: 0;
  }

  .quick-hints {
    flex-direction: column;
    gap: 4px;
  }

  .hint-sep {
    display: none;
  }
}
</style>
