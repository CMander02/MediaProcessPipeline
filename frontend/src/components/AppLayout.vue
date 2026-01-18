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
import type { PageName, NavItem } from "@/types"

defineProps<{
  currentPage: PageName
}>()

const emit = defineEmits<{
  (e: "navigate", page: PageName): void
}>()

const collapsed = ref(true)

const navItems: NavItem[] = [
  { id: "home", label: "Dashboard", icon: "HomeFilled" },
  { id: "tasks", label: "Tasks", icon: "List" },
  { id: "archives", label: "Archives", icon: "FolderOpened" },
  { id: "settings", label: "Settings", icon: "Setting" },
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
      <!-- Logo -->
      <div class="sidebar-header">
        <el-icon class="sidebar-logo">
          <MenuIcon />
        </el-icon>
        <span v-show="!collapsed" class="sidebar-title">MediaPipeline</span>
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
          <span v-show="!collapsed" class="sidebar-nav-item-label">{{ item.label }}</span>
        </div>
      </nav>

      <!-- Toggle -->
      <div class="sidebar-footer">
        <div class="sidebar-toggle" @click="collapsed = !collapsed">
          <el-icon>
            <DArrowRight v-if="collapsed" />
            <DArrowLeft v-else />
          </el-icon>
        </div>
      </div>
    </aside>

    <!-- Main content -->
    <main class="main-content">
      <slot />
    </main>
  </div>
</template>
