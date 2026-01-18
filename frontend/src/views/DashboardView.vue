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
  CaretRight,
  Link,
  FolderOpened,
  Loading,
  CircleCheck,
  CircleClose,
  Clock,
  DataLine,
} from "@element-plus/icons-vue"
import StatCard from "@/components/StatCard.vue"
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
      <p class="page-description">Transform audio/video into structured knowledge</p>
    </div>

    <!-- Main Grid -->
    <div class="grid grid-cols-1 xl:grid-cols-3 gap-8">
      <!-- Left Column -->
      <div class="xl:col-span-2 space-y-8">
        <!-- New Task Card -->
        <div class="custom-card">
          <div class="custom-card-header">
            <h2 class="custom-card-title">New Task</h2>
            <p class="custom-card-description">Enter a URL or local file path to process</p>
          </div>

          <form @submit.prevent="handleSubmit" class="space-y-6">
            <!-- Source Input -->
            <div class="space-y-3">
              <label class="text-sm font-medium">Media Source</label>
              <div class="flex gap-4">
                <div class="relative flex-1">
                  <el-input
                    v-model="source"
                    placeholder="https://youtube.com/watch?v=... or C:\path\to\file.mp4"
                    size="large"
                  >
                    <template #suffix>
                      <el-icon>
                        <Link v-if="isUrl" />
                        <FolderOpened v-else />
                      </el-icon>
                    </template>
                  </el-input>
                </div>
                <el-select v-model="taskType" size="large" style="width: 180px">
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
            <el-collapse>
              <el-collapse-item title="Advanced Options" name="options">
                <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 pt-2">
                  <div class="option-card">
                    <span class="option-card-label">Skip vocal separation</span>
                    <el-switch v-model="options.skip_separation" />
                  </div>
                  <div class="option-card">
                    <span class="option-card-label">Skip speaker diarization</span>
                    <el-switch v-model="options.skip_diarization" />
                  </div>
                  <div class="option-card">
                    <div class="space-y-2">
                      <span class="option-card-label">Language</span>
                      <el-select v-model="options.language" style="width: 100%">
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
              </el-collapse-item>
            </el-collapse>

            <!-- Submit -->
            <el-button
              type="primary"
              size="large"
              :disabled="!source.trim() || submitting"
              :loading="submitting"
              native-type="submit"
              style="width: 100%"
            >
              <el-icon v-if="!submitting" class="mr-1"><CaretRight /></el-icon>
              {{ submitting ? "Creating..." : "Start Processing" }}
            </el-button>
          </form>
        </div>

        <!-- Active Tasks -->
        <div v-if="activeTasks.length > 0" class="custom-card">
          <div class="custom-card-header">
            <h2 class="custom-card-title">Active Tasks</h2>
          </div>
          <div class="space-y-4">
            <div
              v-for="task in activeTasks"
              :key="task.id"
              class="border border-[var(--border-color)] rounded-lg p-4 space-y-3"
            >
              <div class="flex items-center justify-between">
                <div class="flex items-center gap-3 min-w-0">
                  <el-icon class="text-blue-500 animate-spin"><Loading /></el-icon>
                  <span class="font-medium truncate">{{ task.source }}</span>
                </div>
                <el-tag size="small">{{ task.task_type }}</el-tag>
              </div>
              <div class="space-y-2">
                <div class="flex justify-between text-sm text-[var(--text-muted)]">
                  <span>{{ task.message || "Processing..." }}</span>
                  <span>{{ getProgress(task) }}%</span>
                </div>
                <el-progress :percentage="getProgress(task)" :show-text="false" :stroke-width="6" />
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Right Column - Stats -->
      <div class="space-y-6">
        <h2 class="text-lg font-semibold">Statistics</h2>
        <div class="grid grid-cols-2 xl:grid-cols-1 gap-4">
          <StatCard label="Total Tasks" :value="tasks.length">
            <template #icon><DataLine /></template>
          </StatCard>
          <StatCard label="Completed" :value="completed" icon-color="#67c23a">
            <template #icon><CircleCheck /></template>
          </StatCard>
          <StatCard label="Processing" :value="processing" icon-color="#409eff">
            <template #icon><Clock /></template>
          </StatCard>
          <StatCard label="Failed" :value="failed" icon-color="#f56c6c">
            <template #icon><CircleClose /></template>
          </StatCard>
        </div>
      </div>
    </div>
  </div>
</template>
