<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import ReportTemplates from './ReportTemplates.vue'
import TaskbarTemplates from './TaskbarTemplates.vue'

const route = useRoute()
const currentTab = computed(() => route.query.tab === 'taskbar' ? 'taskbar' : 'report')
</script>

<template>
  <section class="page-intro"><div><h1>模板管理</h1><p>管理巡检报告和截图底栏使用的模板。</p></div></section>
  <nav class="template-tabs" aria-label="模板类型">
    <RouterLink :to="{ path: '/templates', query: { tab: 'report' } }" :class="{ active: currentTab === 'report' }" :aria-current="currentTab === 'report' ? 'page' : undefined">报告模板</RouterLink>
    <RouterLink :to="{ path: '/templates', query: { tab: 'taskbar' } }" :class="{ active: currentTab === 'taskbar' }" :aria-current="currentTab === 'taskbar' ? 'page' : undefined">底栏模板</RouterLink>
  </nav>
  <ReportTemplates v-if="currentTab === 'report'" />
  <TaskbarTemplates v-else />
</template>

<style scoped>
.template-tabs{display:flex;gap:8px;margin:0 0 24px;padding:5px;width:max-content;max-width:100%;border:1px solid #dce4e8;border-radius:12px;background:#f5f8f9}
.template-tabs a{display:block;padding:10px 20px;border-radius:8px;color:#536a78;text-decoration:none;font-weight:600;white-space:nowrap}
.template-tabs a.active{background:#fff;color:#087a9b;box-shadow:0 1px 4px rgba(30,63,81,.12)}
</style>
