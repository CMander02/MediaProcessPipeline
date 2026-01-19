<script setup lang="ts">
import { ref, computed } from "vue"
import { ElButton, ElSelect, ElOption, ElSkeleton, ElIcon } from "element-plus"
import { Refresh, WarningFilled } from "@element-plus/icons-vue"
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
  <div class="page-container">
    <!-- Header -->
    <div class="page-header flex-between">
      <div>
        <h1 class="page-title">Tasks</h1>
        <p class="page-description">View and manage processing tasks</p>
      </div>
      <el-button @click="refresh()" :loading="loading">
        <el-icon class="mr-1"><Refresh /></el-icon>
        Refresh
      </el-button>
    </div>

    <!-- Filters -->
    <div class="filters-bar">
      <el-select v-model="filter" class="filter-select">
        <el-option
          v-for="opt in statusOptions"
          :key="opt.value"
          :value="opt.value"
          :label="opt.label"
        />
      </el-select>
      <span class="task-count">{{ filteredTasks.length }} task{{ filteredTasks.length !== 1 ? "s" : "" }}</span>
    </div>

    <!-- Task List Card -->
    <div class="custom-card tasks-card">
      <!-- Loading -->
      <div v-if="loading && tasks.length === 0" class="skeleton-list">
        <el-skeleton v-for="i in 4" :key="i" :rows="2" animated class="skeleton-item" />
      </div>

      <!-- Error -->
      <div v-else-if="error" class="error-state">
        <div class="error-state-icon">
          <el-icon><WarningFilled /></el-icon>
        </div>
        <p class="error-state-text">{{ error }}</p>
        <el-button @click="refresh()" style="margin-top: 16px">
          <el-icon class="mr-1"><Refresh /></el-icon>
          Try Again
        </el-button>
      </div>

      <!-- Empty -->
      <div v-else-if="filteredTasks.length === 0" class="empty-state">
        <!-- Empty State SVG -->
        <svg class="empty-illustration" viewBox="0 0 200 200" fill="none" xmlns="http://www.w3.org/2000/svg">
          <rect x="40" y="60" width="120" height="100" rx="8" fill="#E2E8F0" stroke="#CBD5E1" stroke-width="2"/>
          <rect x="50" y="80" width="100" height="12" rx="4" fill="#CBD5E1"/>
          <rect x="50" y="100" width="80" height="12" rx="4" fill="#CBD5E1"/>
          <rect x="50" y="120" width="60" height="12" rx="4" fill="#CBD5E1"/>
          <circle cx="150" cy="50" r="30" fill="#EEF2FF" stroke="#6366F1" stroke-width="2"/>
          <path d="M140 50L148 58L162 44" stroke="#6366F1" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        <p class="empty-state-title">No tasks found</p>
        <p class="empty-state-text">
          {{ filter === "all"
            ? "Create a new task from the Dashboard to get started"
            : `No ${filter} tasks at the moment` }}
        </p>
      </div>

      <!-- Task List -->
      <div v-else class="task-list">
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

<style scoped>
.filters-bar {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 24px;
}

.filter-select {
  width: 180px;
}

.task-count {
  font-size: 14px;
  color: var(--text-muted);
}

.tasks-card {
  min-height: 400px;
}

.skeleton-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.skeleton-item {
  padding: 16px;
  background: var(--bg-base);
  border-radius: var(--border-radius-sm);
}

.task-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 600px;
  overflow-y: auto;
  padding-right: 8px;
}

/* Empty Illustration */
.empty-illustration {
  width: 200px;
  height: 200px;
  margin-bottom: 16px;
  opacity: 0.8;
}
</style>
