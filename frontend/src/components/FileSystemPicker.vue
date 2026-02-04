<script setup lang="ts">
import { ref, computed, watch, onMounted } from "vue"
import {
  ElDialog,
  ElButton,
  ElInput,
  ElIcon,
  ElBreadcrumb,
  ElBreadcrumbItem,
} from "element-plus"
import {
  FolderOpened,
  Document,
  Back,
  Search,
  RefreshRight,
} from "@element-plus/icons-vue"

const props = defineProps<{
  modelValue: boolean
  mode: "file" | "directory" | "all"
  title?: string
  initialPath?: string
}>()

const emit = defineEmits<{
  (e: "update:modelValue", value: boolean): void
  (e: "select", path: string): void
}>()

interface FileItem {
  name: string
  path: string
  is_dir: boolean
  size?: number
}

interface Drive {
  name: string
  path: string
  is_dir: boolean
}

const currentPath = ref("")
const items = ref<FileItem[]>([])
const drives = ref<Drive[]>([])
const loading = ref(false)
const searchQuery = ref("")
const selectedPath = ref("")

// API base URL
const API_BASE = "http://localhost:18000/api"

// Path segments for breadcrumb
const pathSegments = computed(() => {
  if (!currentPath.value) return []
  const parts = currentPath.value.split(/[/\\]/).filter(Boolean)
  const segments: { name: string; path: string }[] = []

  // Handle Windows drive letters
  if (currentPath.value.match(/^[A-Z]:\\/i)) {
    let accumulated = parts[0] + "\\"
    segments.push({ name: parts[0], path: accumulated })
    for (let i = 1; i < parts.length; i++) {
      accumulated += parts[i] + "\\"
      segments.push({ name: parts[i], path: accumulated })
    }
  } else {
    let accumulated = "/"
    for (const part of parts) {
      accumulated += part + "/"
      segments.push({ name: part, path: accumulated })
    }
  }

  return segments
})

// Filtered items based on search
const filteredItems = computed(() => {
  if (!searchQuery.value) return items.value
  const query = searchQuery.value.toLowerCase()
  return items.value.filter((item) => item.name.toLowerCase().includes(query))
})

const loadDrives = async () => {
  try {
    const res = await fetch(`${API_BASE}/filesystem/drives`)
    const data = await res.json()
    if (data.success) {
      drives.value = data.drives
    }
  } catch (e) {
    console.error("Failed to load drives:", e)
  }
}

const loadDirectory = async (path: string) => {
  loading.value = true
  try {
    const res = await fetch(
      `${API_BASE}/filesystem/browse?path=${encodeURIComponent(path)}&mode=${props.mode}`
    )
    const data = await res.json()
    if (data.success) {
      currentPath.value = data.path
      items.value = data.items
    } else {
      console.error("Browse error:", data.error)
    }
  } catch (e) {
    console.error("Failed to browse:", e)
  } finally {
    loading.value = false
  }
}

const handleItemClick = (item: FileItem) => {
  if (item.is_dir) {
    loadDirectory(item.path)
    selectedPath.value = ""
  } else {
    selectedPath.value = item.path
  }
}

const handleItemDblClick = (item: FileItem) => {
  if (item.is_dir) {
    if (props.mode === "directory") {
      confirmSelection(item.path)
    } else {
      loadDirectory(item.path)
    }
  } else {
    confirmSelection(item.path)
  }
}

const handleDriveClick = (drive: Drive) => {
  loadDirectory(drive.path)
}

const handleBreadcrumbClick = (path: string) => {
  loadDirectory(path)
}

const confirmSelection = (path?: string) => {
  const finalPath = path || selectedPath.value || currentPath.value
  if (finalPath) {
    emit("select", finalPath)
    emit("update:modelValue", false)
  }
}

const formatSize = (bytes: number | undefined): string => {
  if (bytes === undefined || bytes === null) return ""
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`
}

watch(
  () => props.modelValue,
  (visible) => {
    if (visible) {
      loadDrives()
      if (props.initialPath) {
        loadDirectory(props.initialPath)
      } else {
        // Default to user home or current directory
        loadDirectory("~")
      }
    }
  }
)

onMounted(() => {
  if (props.modelValue) {
    loadDrives()
    loadDirectory(props.initialPath || "~")
  }
})
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    @update:model-value="emit('update:modelValue', $event)"
    :title="title || (mode === 'directory' ? '选择文件夹' : '选择文件')"
    width="700px"
    class="file-picker-dialog"
  >
    <!-- Toolbar -->
    <div class="picker-toolbar">
      <div class="drives-bar">
        <button
          v-for="drive in drives"
          :key="drive.path"
          class="drive-btn"
          :class="{ active: currentPath.startsWith(drive.path) }"
          @click="handleDriveClick(drive)"
        >
          {{ drive.name }}
        </button>
      </div>
      <div class="search-bar">
        <el-input
          v-model="searchQuery"
          placeholder="搜索..."
          size="small"
          clearable
          :prefix-icon="Search"
        />
        <el-button
          size="small"
          :icon="RefreshRight"
          @click="loadDirectory(currentPath)"
          :loading="loading"
        />
      </div>
    </div>

    <!-- Breadcrumb -->
    <div class="picker-breadcrumb">
      <el-breadcrumb separator="/">
        <el-breadcrumb-item>
          <button class="breadcrumb-btn" @click="loadDirectory('~')">主目录</button>
        </el-breadcrumb-item>
        <el-breadcrumb-item v-for="seg in pathSegments" :key="seg.path">
          <button class="breadcrumb-btn" @click="handleBreadcrumbClick(seg.path)">
            {{ seg.name }}
          </button>
        </el-breadcrumb-item>
      </el-breadcrumb>
    </div>

    <!-- File list -->
    <div class="picker-list" :class="{ loading }">
      <div
        v-for="item in filteredItems"
        :key="item.path"
        class="picker-item"
        :class="{ selected: selectedPath === item.path, 'is-dir': item.is_dir }"
        @click="handleItemClick(item)"
        @dblclick="handleItemDblClick(item)"
      >
        <el-icon class="item-icon">
          <FolderOpened v-if="item.is_dir" />
          <Document v-else />
        </el-icon>
        <span class="item-name">{{ item.name }}</span>
        <span v-if="!item.is_dir" class="item-size">{{ formatSize(item.size) }}</span>
      </div>
      <div v-if="filteredItems.length === 0 && !loading" class="picker-empty">
        无内容
      </div>
    </div>

    <!-- Selected path -->
    <div class="picker-selected">
      <span class="selected-label">选中路径:</span>
      <el-input
        :model-value="selectedPath || currentPath"
        readonly
        size="small"
        class="selected-input"
      />
    </div>

    <!-- Actions -->
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">取消</el-button>
      <el-button
        v-if="mode === 'directory'"
        type="primary"
        @click="confirmSelection(currentPath)"
      >
        选择当前文件夹
      </el-button>
      <el-button
        v-else
        type="primary"
        :disabled="!selectedPath"
        @click="confirmSelection()"
      >
        选择
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.picker-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
  gap: 16px;
}

.drives-bar {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}

.drive-btn {
  padding: 4px 10px;
  background: var(--bg-base);
  border: 1px solid var(--border-color);
  border-radius: 4px;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s;
}

.drive-btn:hover {
  background: var(--border-color);
}

.drive-btn.active {
  background: var(--primary-bg);
  border-color: var(--primary-color);
  color: var(--primary-color);
}

.search-bar {
  display: flex;
  gap: 8px;
  align-items: center;
}

.search-bar .el-input {
  width: 180px;
}

.picker-breadcrumb {
  padding: 8px 12px;
  background: var(--bg-base);
  border-radius: 6px;
  margin-bottom: 12px;
}

.breadcrumb-btn {
  background: none;
  border: none;
  padding: 0;
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 13px;
}

.breadcrumb-btn:hover {
  color: var(--primary-color);
}

.picker-list {
  height: 300px;
  overflow-y: auto;
  border: 1px solid var(--border-color);
  border-radius: 6px;
  background: var(--bg-base);
}

.picker-list.loading {
  opacity: 0.6;
  pointer-events: none;
}

.picker-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  cursor: pointer;
  border-bottom: 1px solid var(--border-color);
  transition: background 0.1s;
}

.picker-item:last-child {
  border-bottom: none;
}

.picker-item:hover {
  background: var(--bg-elevated);
}

.picker-item.selected {
  background: var(--primary-bg);
}

.picker-item.is-dir .item-name {
  font-weight: 500;
}

.item-icon {
  font-size: 18px;
  color: var(--text-muted);
  flex-shrink: 0;
}

.picker-item.is-dir .item-icon {
  color: var(--primary-color);
}

.item-name {
  flex: 1;
  font-size: 14px;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.item-size {
  font-size: 12px;
  color: var(--text-muted);
  flex-shrink: 0;
}

.picker-empty {
  padding: 40px;
  text-align: center;
  color: var(--text-muted);
}

.picker-selected {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 12px;
}

.selected-label {
  font-size: 13px;
  color: var(--text-secondary);
  white-space: nowrap;
}

.selected-input {
  flex: 1;
}

.selected-input :deep(.el-input__inner) {
  background: var(--bg-base);
}
</style>
