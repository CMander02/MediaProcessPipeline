<script setup lang="ts">
import { ref } from "vue"
import AppLayout from "@/components/AppLayout.vue"
import DashboardView from "@/views/DashboardView.vue"
import TasksView from "@/views/TasksView.vue"
import ArchivesView from "@/views/ArchivesView.vue"
import SettingsView from "@/views/SettingsView.vue"
import type { PageName } from "@/types"

const currentPage = ref<PageName>("home")

const handleNavigate = (page: PageName) => {
  currentPage.value = page
}
</script>

<template>
  <AppLayout :current-page="currentPage" @navigate="handleNavigate">
    <DashboardView v-if="currentPage === 'home'" />
    <TasksView v-else-if="currentPage === 'tasks'" />
    <ArchivesView v-else-if="currentPage === 'archives'" />
    <SettingsView v-else-if="currentPage === 'settings'" />
  </AppLayout>
</template>
