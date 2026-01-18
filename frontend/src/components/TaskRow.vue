<script setup lang="ts">
import { computed } from "vue"
import { ElButton, ElTag, ElProgress, ElIcon } from "element-plus"
import {
  Clock,
  Loading,
  CircleCheck,
  CircleClose,
  Close,
  ArrowRight,
  Download,
  Microphone,
  Document,
  Cpu,
  Menu,
} from "@element-plus/icons-vue"
import type { Task, TaskStatus, TaskType } from "@/types"

const props = defineProps<{
  task: Task
}>()

const emit = defineEmits<{
  (e: "click"): void
  (e: "cancel"): void
}>()

const statusConfig: Record<TaskStatus, { label: string; icon: typeof Clock; color: string }> = {
  pending: { label: "Pending", icon: Clock, color: "#909399" },
  queued: { label: "Queued", icon: Clock, color: "#e6a23c" },
  processing: { label: "Processing", icon: Loading, color: "#409eff" },
  completed: { label: "Completed", icon: CircleCheck, color: "#67c23a" },
  failed: { label: "Failed", icon: CircleClose, color: "#f56c6c" },
  cancelled: { label: "Cancelled", icon: Close, color: "#909399" },
}

const typeConfig: Record<TaskType, { label: string; icon: typeof Menu }> = {
  pipeline: { label: "Full Pipeline", icon: Menu },
  ingestion: { label: "Download", icon: Download },
  preprocessing: { label: "Preprocessing", icon: Microphone },
  recognition: { label: "Transcription", icon: Document },
  analysis: { label: "Analysis", icon: Cpu },
  archiving: { label: "Archiving", icon: Document },
}

const status = computed(() => statusConfig[props.task.status])
const type = computed(() => typeConfig[props.task.task_type])
const progress = computed(() => Math.round(props.task.progress * 100))
const isActive = computed(() =>
  props.task.status === "queued" || props.task.status === "processing"
)

const handleCancel = (e: Event) => {
  e.stopPropagation()
  emit("cancel")
}
</script>

<template>
  <div class="task-row" @click="emit('click')">
    <!-- Status Icon -->
    <el-icon
      :style="{ color: status.color }"
      :class="{ 'animate-spin': task.status === 'processing' }"
    >
      <component :is="status.icon" />
    </el-icon>

    <!-- Task Info -->
    <div class="task-row-content">
      <div class="flex items-center gap-2">
        <span class="task-row-source">{{ task.source }}</span>
        <el-tag size="small" type="info">
          <el-icon class="mr-1"><component :is="type.icon" /></el-icon>
          {{ type.label }}
        </el-tag>
      </div>
      <div class="task-row-meta">
        <span>{{ new Date(task.created_at).toLocaleString() }}</span>
        <span v-if="task.message">{{ task.message }}</span>
      </div>
      <el-progress
        v-if="isActive"
        :percentage="progress"
        :show-text="false"
        :stroke-width="4"
        class="mt-2"
      />
    </div>

    <!-- Actions -->
    <div class="flex items-center gap-2">
      <el-button v-if="isActive" size="small" text @click="handleCancel">Cancel</el-button>
      <el-icon class="text-[var(--text-muted)]"><ArrowRight /></el-icon>
    </div>
  </div>
</template>
