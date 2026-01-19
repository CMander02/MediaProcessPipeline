<script setup lang="ts">
import { ref, computed, onMounted } from "vue"
import { ElButton, ElInput, ElSkeleton, ElIcon } from "element-plus"
import { Refresh, Search, WarningFilled } from "@element-plus/icons-vue"
import ArchiveCard from "@/components/ArchiveCard.vue"
import ArchiveDetailDialog from "@/components/ArchiveDetailDialog.vue"
import { pipelineApi } from "@/api"
import type { ArchiveItem } from "@/types"

const archives = ref<ArchiveItem[]>([])
const loading = ref(true)
const error = ref<string | null>(null)
const search = ref("")
const selected = ref<ArchiveItem | null>(null)
const dialogVisible = ref(false)

const fetchArchives = async () => {
  loading.value = true
  try {
    const res = await pipelineApi.archives()
    archives.value = res.archives || []
    error.value = null
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Failed to fetch archives"
  } finally {
    loading.value = false
  }
}

const filtered = computed(() =>
  archives.value.filter((a) =>
    a.title.toLowerCase().includes(search.value.toLowerCase())
  )
)

const handleArchiveClick = (archive: ArchiveItem) => {
  selected.value = archive
  dialogVisible.value = true
}

onMounted(() => {
  fetchArchives()
})
</script>

<template>
  <div class="page-container">
    <!-- Header -->
    <div class="page-header flex-between">
      <div>
        <h1 class="page-title">Archives</h1>
        <p class="page-description">Browse processed media results</p>
      </div>
      <el-button @click="fetchArchives" :loading="loading">
        <el-icon class="mr-1"><Refresh /></el-icon>
        Refresh
      </el-button>
    </div>

    <!-- Search Bar -->
    <div class="search-bar">
      <el-input
        v-model="search"
        placeholder="Search archives..."
        class="search-input"
        size="large"
      >
        <template #prefix>
          <el-icon class="search-icon"><Search /></el-icon>
        </template>
      </el-input>
      <span class="archive-count">
        {{ search ? `${filtered.length} of ${archives.length}` : `${archives.length}` }} archive{{ archives.length !== 1 ? "s" : "" }}
      </span>
    </div>

    <!-- Archives Grid Card -->
    <div class="custom-card archives-card">
      <!-- Loading -->
      <div v-if="loading" class="skeleton-grid">
        <el-skeleton v-for="i in 6" :key="i" :rows="3" animated class="skeleton-item" />
      </div>

      <!-- Error -->
      <div v-else-if="error" class="error-state">
        <div class="error-state-icon">
          <el-icon><WarningFilled /></el-icon>
        </div>
        <p class="error-state-text">{{ error }}</p>
        <el-button @click="fetchArchives" style="margin-top: 16px">
          <el-icon class="mr-1"><Refresh /></el-icon>
          Try Again
        </el-button>
      </div>

      <!-- Empty -->
      <div v-else-if="filtered.length === 0" class="empty-state">
        <!-- Empty State SVG -->
        <svg class="empty-illustration" viewBox="0 0 200 200" fill="none" xmlns="http://www.w3.org/2000/svg">
          <rect x="30" y="80" width="140" height="90" rx="8" fill="#E2E8F0" stroke="#CBD5E1" stroke-width="2"/>
          <path d="M30 100H170" stroke="#CBD5E1" stroke-width="2"/>
          <rect x="50" y="115" width="40" height="40" rx="4" fill="#CBD5E1"/>
          <rect x="110" y="115" width="40" height="40" rx="4" fill="#CBD5E1"/>
          <circle cx="100" cy="55" r="25" fill="#EEF2FF" stroke="#6366F1" stroke-width="2"/>
          <rect x="90" y="45" width="20" height="20" rx="2" fill="#6366F1"/>
        </svg>
        <p class="empty-state-title">
          {{ search ? "No matching archives" : "No archives yet" }}
        </p>
        <p class="empty-state-text">
          {{ search
            ? "Try a different search term"
            : "Process some media files to see results here" }}
        </p>
      </div>

      <!-- Archive Grid -->
      <div v-else class="archive-grid">
        <ArchiveCard
          v-for="archive in filtered"
          :key="archive.path"
          :archive="archive"
          @click="handleArchiveClick(archive)"
        />
      </div>
    </div>

    <!-- Archive Detail Dialog -->
    <ArchiveDetailDialog
      :archive="selected"
      v-model:visible="dialogVisible"
    />
  </div>
</template>

<style scoped>
.search-bar {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 24px;
}

.search-input {
  max-width: 400px;
}

.search-input :deep(.el-input__wrapper) {
  background: var(--bg-elevated);
  padding: 8px 16px;
}

.search-icon {
  color: var(--text-muted);
}

.archive-count {
  font-size: 14px;
  color: var(--text-muted);
}

.archives-card {
  min-height: 400px;
}

.skeleton-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 20px;
}

.skeleton-item {
  padding: 20px;
  background: var(--bg-base);
  border-radius: var(--border-radius);
}

.archive-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 20px;
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
