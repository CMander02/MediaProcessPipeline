<script setup lang="ts">
import { ref } from "vue"
import { ElIcon } from "element-plus"
import {
  HomeFilled,
  List,
  FolderOpened,
  Setting,
  DArrowLeft,
  DArrowRight,
  Menu as MenuIcon,
} from "@element-plus/icons-vue"
import { useLocale } from "@/composables/useLocale"
import type { PageName, NavItem } from "@/types"

defineProps<{
  currentPage: PageName
}>()

const emit = defineEmits<{
  (e: "navigate", page: PageName): void
}>()

const { t } = useLocale()

const collapsed = ref(true)

const navItems: NavItem[] = [
  { id: "home", label: "nav.dashboard", icon: "HomeFilled" },
  { id: "tasks", label: "nav.tasks", icon: "List" },
  { id: "archives", label: "nav.archives", icon: "FolderOpened" },
  { id: "settings", label: "nav.settings", icon: "Setting" },
]

const iconMap = {
  HomeFilled,
  List,
  FolderOpened,
  Setting,
}
</script>

<template>
  <div class="app-layout">
    <!-- Sidebar -->
    <aside class="sidebar" :class="{ collapsed }">
      <!-- Logo + Toggle -->
      <div class="sidebar-header">
        <el-icon class="sidebar-logo">
          <MenuIcon />
        </el-icon>
        <span v-if="!collapsed" class="sidebar-title">MediaPipeline</span>
        <!-- 展开/收起按钮始终在header右侧 -->
        <button
          class="sidebar-collapse-btn"
          @click="collapsed = !collapsed"
          :title="collapsed ? '展开侧栏' : '收起侧栏'"
        >
          <el-icon>
            <DArrowRight v-if="collapsed" />
            <DArrowLeft v-else />
          </el-icon>
        </button>
      </div>

      <!-- Navigation -->
      <nav class="sidebar-nav">
        <div
          v-for="item in navItems"
          :key="item.id"
          class="sidebar-nav-item"
          :class="{ active: currentPage === item.id }"
          @click="emit('navigate', item.id)"
        >
          <el-icon class="sidebar-nav-item-icon">
            <component :is="iconMap[item.icon as keyof typeof iconMap]" />
          </el-icon>
          <span v-if="!collapsed" class="sidebar-nav-item-label">{{ t(item.label) }}</span>
        </div>
      </nav>
    </aside>

    <!-- Main content -->
    <main class="main-content">
      <slot />
    </main>
  </div>
</template>

<style scoped>
.sidebar-header {
  display: flex;
  align-items: center;
  gap: 12px;
  height: var(--header-height);
  padding: 0 16px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
}

.sidebar.collapsed .sidebar-header {
  justify-content: center;
  padding: 0;
}

.sidebar-collapse-btn {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: none;
  border-radius: 6px;
  color: var(--text-sidebar);
  cursor: pointer;
  transition: all 0.15s ease;
  margin-left: auto;
  flex-shrink: 0;
}

.sidebar.collapsed .sidebar-collapse-btn {
  margin-left: 0;
}

.sidebar-collapse-btn:hover {
  background: var(--bg-sidebar-hover);
  color: #fff;
}
</style>
