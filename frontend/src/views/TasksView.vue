<script setup lang="ts">
import { ref, computed } from "vue"
import { ElButton, ElSelect, ElOption, ElSkeleton, ElIcon } from "element-plus"
import { Refresh, WarningFilled, FolderOpened } from "@element-plus/icons-vue"
import TaskRow from "@/components/TaskRow.vue"
import TaskDetailDialog from "@/components/TaskDetailDialog.vue"
import { useTasks } from "@/composables/useTasks"
import type { TaskStatus } from "@/types"

const { tasks, loading, error, refresh, cancelTask } = useTasks()
const filter = ref<TaskStatus | "all">("all")
const selectedTaskId = ref<string | null>(null)
const dialogVisible = ref(false)

const filteredTasks = computed(() =>
  filter.value === "all" ? tasks.value : tasks.value.filter((t) => t.status === filter.value)
)

const statusOptions = [
  { value: "all", label: "All Tasks" },
  { value: "queued", label: "Queued" },
  { value: "processing", label: "Processing" },
  { value: "completed", label: "Completed" },
  { value: "failed", label: "Failed" },
  { value: "cancelled", label: "Cancelled" },
]

const handleTaskClick = (taskId: string) => {
  selectedTaskId.value = taskId
  dialogVisible.value = true
}

const handleCancelTask = (taskId: string) => {
  cancelTask(taskId)
}
</script>

<template>
  <div class="page-container space-y-8">
    <!-- Header -->
    <div class="flex-between">
      <div>
        <h1 class="page-title">Tasks</h1>
        <p class="page-description">View and manage processing tasks</p>
      </div>
      <el-button @click="refresh()">
        <el-icon class="mr-1"><Refresh /></el-icon>
        Refresh
      </el-button>
    </div>

    <!-- Filters -->
    <div class="flex gap-4">
      <el-select v-model="filter" style="width: 160px">
        <el-option
          v-for="opt in statusOptions"
          :key="opt.value"
          :value="opt.value"
          :label="opt.label"
        />
      </el-select>
    </div>

    <!-- Task List -->
    <div class="custom-card">
      <div class="custom-card-header">
        <h2 class="custom-card-title">
          {{ filter === "all" ? "All Tasks" : `${filter} Tasks` }}
        </h2>
        <p class="custom-card-description">
          {{ filteredTasks.length }} task{{ filteredTasks.length !== 1 ? "s" : "" }}
        </p>
      </div>

      <!-- Loading -->
      <div v-if="loading && tasks.length === 0" class="space-y-3">
        <el-skeleton v-for="i in 3" :key="i" :rows="2" animated />
      </div>

      <!-- Error -->
      <div v-else-if="error" class="flex items-center gap-2 text-[#f56c6c] py-4">
        <el-icon><WarningFilled /></el-icon>
        <span>{{ error }}</span>
      </div>

      <!-- Empty -->
      <div v-else-if="filteredTasks.length === 0" class="empty-state">
        <el-icon class="empty-state-icon"><FolderOpened /></el-icon>
        <p class="empty-state-text">No tasks found</p>
      </div>

      <!-- Task List -->
      <div v-else class="space-y-2 max-h-[500px] overflow-auto">
        <TaskRow
          v-for="task in filteredTasks"
          :key="task.id"
          :task="task"
          @click="handleTaskClick(task.id)"
          @cancel="handleCancelTask(task.id)"
        />
      </div>
    </div>

    <!-- Task Detail Dialog -->
    <TaskDetailDialog
      :task-id="selectedTaskId"
      v-model:visible="dialogVisible"
    />
  </div>
</template>
