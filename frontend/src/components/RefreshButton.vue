<script setup lang="ts">
import { RefreshCw } from '../icons'
defineProps<{ loading?: boolean }>()
defineEmits<{ click: [] }>()
</script>

<template>
  <IconButton toolbar :loading="loading" :label="loading ? '正在刷新' : '刷新列表'" class="toolbar-icon-button" :disabled="loading" :aria-label="loading ? '正在刷新' : '刷新'" :title="loading ? '正在刷新' : '刷新'" @click="$emit('click')">
    <RefreshCw :size="17" :class="{ 'spin': loading }" />
  </IconButton>
</template>
