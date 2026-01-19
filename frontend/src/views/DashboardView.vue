<script setup lang="ts">
import { ref, computed } from "vue"
import {
  ElButton,
  ElInput,
  ElSelect,
  ElOption,
  ElSwitch,
  ElCollapse,
  ElCollapseItem,
  ElTag,
  ElProgress,
  ElIcon,
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
} from "@element-plus/icons-vue"
import { useTasks } from "@/composables/useTasks"
import type { TaskType, PipelineOptions, Task } from "@/types"

const { tasks, createTask } = useTasks()

const source = ref("")
const taskType = ref<TaskType>("pipeline")
const submitting = ref(false)
const options = ref<PipelineOptions>({
  skip_download: false,
  skip_separation: false,
  skip_diarization: false,
  language: "auto",
})

const isUrl = computed(() =>
  source.value.startsWith("http://") || source.value.startsWith("https://")
)

const handleSubmit = async () => {
  if (!source.value.trim()) return

  submitting.value = true
  try {
    await createTask({
      task_type: taskType.value,
      source: source.value.trim(),
      options: { ...options.value },
    })
    source.value = ""
  } catch (error) {
    console.error("Failed to create task:", error)
  } finally {
    submitting.value = false
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

const taskTypeOptions = [
  { value: "pipeline", label: "Full Pipeline" },
  { value: "ingestion", label: "Download Only" },
  { value: "recognition", label: "Transcribe Only" },
  { value: "analysis", label: "Analyze Only" },
]

const languageOptions = [
  { value: "auto", label: "Auto Detect" },
  { value: "en", label: "English" },
  { value: "zh", label: "Chinese" },
  { value: "ja", label: "Japanese" },
  { value: "ko", label: "Korean" },
  { value: "de", label: "German" },
  { value: "fr", label: "French" },
  { value: "es", label: "Spanish" },
]
</script>

<template>
  <div class="page-container">
    <!-- Header -->
    <div class="page-header">
      <h1 class="page-title">Media Processing Pipeline</h1>
      <p class="page-description">Transform audio/video into structured knowledge with AI</p>
    </div>

    <!-- Stats Grid - Top Horizontal -->
    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-card-icon total">
          <el-icon><DataLine /></el-icon>
        </div>
        <div class="stat-card-content">
          <div class="stat-card-value">{{ tasks.length }}</div>
          <div class="stat-card-label">Total Tasks</div>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-card-icon success">
          <el-icon><CircleCheck /></el-icon>
        </div>
        <div class="stat-card-content">
          <div class="stat-card-value">{{ completed }}</div>
          <div class="stat-card-label">Completed</div>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-card-icon processing">
          <el-icon :class="{ 'animate-spin': processing > 0 }"><Clock /></el-icon>
        </div>
        <div class="stat-card-content">
          <div class="stat-card-value">{{ processing }}</div>
          <div class="stat-card-label">Processing</div>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-card-icon error">
          <el-icon><CircleClose /></el-icon>
        </div>
        <div class="stat-card-content">
          <div class="stat-card-value">{{ failed }}</div>
          <div class="stat-card-label">Failed</div>
        </div>
      </div>
    </div>

    <!-- Main Content -->
    <div class="dashboard-grid">
      <!-- New Task Card -->
      <div class="custom-card new-task-card">
        <div class="custom-card-header">
          <h2 class="custom-card-title">Create New Task</h2>
          <p class="custom-card-description">Enter a URL or local file path to start processing</p>
        </div>

        <form @submit.prevent="handleSubmit" class="task-form">
          <!-- Source Input -->
          <div class="input-group">
            <label class="input-label">Media Source</label>
            <div class="input-row">
              <el-input
                v-model="source"
                placeholder="https://youtube.com/watch?v=... or C:\path\to\file.mp4"
                size="large"
                class="source-input"
              >
                <template #prefix>
                  <el-icon class="input-prefix-icon">
                    <Link v-if="isUrl" />
                    <FolderOpened v-else />
                  </el-icon>
                </template>
              </el-input>
              <el-select v-model="taskType" size="large" class="type-select">
                <el-option
                  v-for="opt in taskTypeOptions"
                  :key="opt.value"
                  :value="opt.value"
                  :label="opt.label"
                />
              </el-select>
            </div>
          </div>

          <!-- Advanced Options -->
          <el-collapse class="options-collapse">
            <el-collapse-item title="Advanced Options" name="options">
              <div class="options-grid">
                <div class="option-card">
                  <span class="option-card-label">Skip vocal separation</span>
                  <el-switch v-model="options.skip_separation" />
                </div>
                <div class="option-card">
                  <span class="option-card-label">Skip speaker diarization</span>
                  <el-switch v-model="options.skip_diarization" />
                </div>
                <div class="option-card language-option">
                  <span class="option-card-label">Language</span>
                  <el-select v-model="options.language" size="default">
                    <el-option
                      v-for="opt in languageOptions"
                      :key="opt.value"
                      :value="opt.value"
                      :label="opt.label"
                    />
                  </el-select>
                </div>
              </div>
            </el-collapse-item>
          </el-collapse>

          <!-- Submit Button -->
          <el-button
            type="primary"
            size="large"
            :disabled="!source.trim() || submitting"
            :loading="submitting"
            native-type="submit"
            class="submit-btn"
          >
            <el-icon v-if="!submitting" class="btn-icon"><VideoPlay /></el-icon>
            {{ submitting ? "Creating Task..." : "Start Processing" }}
          </el-button>
        </form>
      </div>

      <!-- Active Tasks -->
      <div class="custom-card active-tasks-card">
        <div class="custom-card-header">
          <h2 class="custom-card-title">Active Tasks</h2>
          <p class="custom-card-description">Currently processing</p>
        </div>

        <div v-if="activeTasks.length > 0" class="active-tasks-list">
          <div
            v-for="task in activeTasks"
            :key="task.id"
            class="active-task-item"
          >
            <div class="task-header">
              <div class="task-info">
                <el-icon class="task-spinner"><Loading /></el-icon>
                <span class="task-source">{{ task.source }}</span>
              </div>
              <el-tag size="small" type="info">{{ task.task_type }}</el-tag>
            </div>
            <div class="task-progress">
              <div class="progress-info">
                <span class="progress-message">{{ task.message || "Processing..." }}</span>
                <span class="progress-percent">{{ getProgress(task) }}%</span>
              </div>
              <el-progress
                :percentage="getProgress(task)"
                :show-text="false"
                :stroke-width="8"
              />
            </div>
          </div>
        </div>

        <div v-else class="empty-state">
          <div class="empty-state-icon">
            <el-icon><Clock /></el-icon>
          </div>
          <p class="empty-state-title">No Active Tasks</p>
          <p class="empty-state-text">Create a new task to start processing media files</p>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.dashboard-grid {
  display: grid;
  grid-template-columns: 1.5fr 1fr;
  gap: 24px;
}

@media (max-width: 1200px) {
  .dashboard-grid {
    grid-template-columns: 1fr;
  }
}

/* New Task Card */
.new-task-card {
  min-height: 400px;
}

.task-form {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.input-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.input-label {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-secondary);
}

.input-row {
  display: flex;
  gap: 12px;
}

.source-input {
  flex: 1;
}

.source-input :deep(.el-input__wrapper) {
  padding: 8px 16px;
  background: var(--bg-base);
}

.source-input :deep(.el-input__wrapper):hover {
  background: var(--bg-elevated);
}

.input-prefix-icon {
  color: var(--text-muted);
  margin-right: 4px;
}

.type-select {
  width: 180px;
  flex-shrink: 0;
}

.options-collapse {
  background: transparent;
}

.options-collapse :deep(.el-collapse-item__header) {
  height: 44px;
  font-size: 14px;
}

.options-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 12px;
  padding-top: 8px;
}

.language-option {
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
}

.language-option .el-select {
  width: 100%;
}

.submit-btn {
  height: 52px;
  font-size: 16px;
  font-weight: 600;
  border-radius: 10px;
}

.btn-icon {
  margin-right: 8px;
  font-size: 18px;
}

/* Active Tasks Card */
.active-tasks-card {
  display: flex;
  flex-direction: column;
}

.active-tasks-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.active-task-item {
  padding: 16px;
  background: var(--bg-base);
  border-radius: var(--border-radius-sm);
  transition: all 0.2s ease;
}

.active-task-item:hover {
  background: #e8eaed;
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
  animation: spin 1s linear infinite;
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

.task-progress {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.progress-info {
  display: flex;
  justify-content: space-between;
  font-size: 13px;
}

.progress-message {
  color: var(--text-muted);
}

.progress-percent {
  color: var(--primary-color);
  font-weight: 600;
}

/* Empty State */
.active-tasks-card .empty-state {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px 20px;
}

.active-tasks-card .empty-state-icon {
  width: 64px;
  height: 64px;
  font-size: 28px;
}
</style>
