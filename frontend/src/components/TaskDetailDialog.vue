<script setup lang="ts">
import { computed } from "vue"
import { ElDialog, ElProgress, ElSkeleton, ElIcon } from "element-plus"
import {
  Clock,
  Loading,
  CircleCheck,
  CircleClose,
  Close,
  WarningFilled,
} from "@element-plus/icons-vue"
import { useTask } from "@/composables/useTasks"
import type { TaskStatus, TaskType } from "@/types"

const props = defineProps<{
  taskId: string | null
  visible: boolean
}>()

const emit = defineEmits<{
  (e: "update:visible", value: boolean): void
}>()

const { task, loading, error } = useTask(() => props.taskId)

const statusConfig: Record<TaskStatus, { label: string; icon: typeof Clock; color: string }> = {
  pending: { label: "Pending", icon: Clock, color: "#909399" },
  queued: { label: "Queued", icon: Clock, color: "#e6a23c" },
  processing: { label: "Processing", icon: Loading, color: "#409eff" },
  completed: { label: "Completed", icon: CircleCheck, color: "#67c23a" },
  failed: { label: "Failed", icon: CircleClose, color: "#f56c6c" },
  cancelled: { label: "Cancelled", icon: Close, color: "#909399" },
}

const typeConfig: Record<TaskType, { label: string }> = {
  pipeline: { label: "Full Pipeline" },
  ingestion: { label: "Download" },
  preprocessing: { label: "Preprocessing" },
  recognition: { label: "Transcription" },
  analysis: { label: "Analysis" },
  archiving: { label: "Archiving" },
}

const status = computed(() => task.value ? statusConfig[task.value.status] : null)
const progress = computed(() => task.value ? Math.round(task.value.progress * 100) : 0)
const isActive = computed(() =>
  task.value && (task.value.status === "queued" || task.value.status === "processing")
)

const handleClose = () => {
  emit("update:visible", false)
}
</script>

<template>
  <el-dialog
    :model-value="visible"
    @update:model-value="handleClose"
    title="Task Details"
    width="640px"
    :close-on-click-modal="true"
  >
    <template #header>
      <div class="flex items-center gap-2">
        <el-icon
          v-if="status"
          :style="{ color: status.color }"
          :class="{ 'animate-spin': task?.status === 'processing' }"
        >
          <component :is="status.icon" />
        </el-icon>
        <span>Task Details</span>
      </div>
    </template>

    <div v-if="loading && !task" class="space-y-3">
      <el-skeleton :rows="3" animated />
    </div>

    <div v-else-if="error" class="flex items-center gap-2 text-[#f56c6c]">
      <el-icon><WarningFilled /></el-icon>
      {{ error }}
    </div>

    <div v-else-if="task" class="space-y-4">
      <!-- Source -->
      <p class="text-[var(--text-muted)] text-sm">{{ task.source }}</p>

      <!-- Progress -->
      <div v-if="isActive" class="space-y-2">
        <div class="flex justify-between text-sm">
          <span>{{ task.message || "Processing..." }}</span>
          <span>{{ progress }}%</span>
        </div>
        <el-progress :percentage="progress" :stroke-width="8" />
      </div>

      <!-- Error -->
      <div v-if="task.error" class="bg-[#fef0f0] text-[#f56c6c] p-3 rounded-lg text-sm">
        <strong>Error:</strong> {{ task.error }}
      </div>

      <!-- Details Grid -->
      <div class="grid grid-cols-2 gap-4 text-sm">
        <div>
          <p class="text-[var(--text-muted)]">ID</p>
          <p class="font-mono text-xs">{{ task.id }}</p>
        </div>
        <div>
          <p class="text-[var(--text-muted)]">Type</p>
          <p>{{ typeConfig[task.task_type].label }}</p>
        </div>
        <div>
          <p class="text-[var(--text-muted)]">Created</p>
          <p>{{ new Date(task.created_at).toLocaleString() }}</p>
        </div>
        <div>
          <p class="text-[var(--text-muted)]">Updated</p>
          <p>{{ new Date(task.updated_at).toLocaleString() }}</p>
        </div>
        <div v-if="task.completed_at">
          <p class="text-[var(--text-muted)]">Completed</p>
          <p>{{ new Date(task.completed_at).toLocaleString() }}</p>
        </div>
      </div>

      <!-- Result -->
      <div v-if="task.result">
        <p class="text-[var(--text-muted)] text-sm mb-2">Result</p>
        <pre class="bg-[var(--bg-secondary)] p-3 rounded-lg text-xs overflow-auto max-h-60">{{ JSON.stringify(task.result, null, 2) }}</pre>
      </div>

      <!-- Options -->
      <div v-if="Object.keys(task.options).length > 0">
        <p class="text-[var(--text-muted)] text-sm mb-2">Options</p>
        <pre class="bg-[var(--bg-secondary)] p-3 rounded-lg text-xs">{{ JSON.stringify(task.options, null, 2) }}</pre>
      </div>
    </div>
  </el-dialog>
</template>
