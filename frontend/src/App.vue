<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ClipboardList, History, FileArchive, KeyRound, Bell, Settings2, LogOut, Menu, X, CheckCircle2, AlertCircle, Info } from './icons'
import { logout } from './api'
import { startIdleSession } from './sessionIdle'
import { notice, confirmState, handleConfirm } from './ui'
import { LOGO_DATA_URL } from './assets/logo'

const route = useRoute()
const appName = window.APP_CONFIG?.appName || ''
const menuOpen = ref(false)
const confirmButton = ref<{ focus: () => void } | null>(null)
let previousFocus: HTMLElement | null = null
watch(() => route.fullPath, () => { menuOpen.value = false })
watch(() => confirmState.open, async (open) => {
  if (open) {
    previousFocus = document.activeElement as HTMLElement | null
    await nextTick()
    confirmButton.value?.focus()
  } else {
    previousFocus?.focus()
    previousFocus = null
  }
})
function onEscape(event: KeyboardEvent) {
  if (event.key === 'Escape' && confirmState.open) handleConfirm(false)
}
let stopIdleSession: (() => void) | undefined
onMounted(() => {
  window.addEventListener('keydown', onEscape)
  stopIdleSession = startIdleSession()
})
onUnmounted(() => {
  window.removeEventListener('keydown', onEscape)
  stopIdleSession?.()
})
const navigation = [
  { path: '/jobs', label: '巡检任务', icon: ClipboardList },
  { path: '/runs', label: '巡检记录', icon: History },
  { path: '/periodic', label: '周期报告', icon: FileArchive },
  { path: '/templates', label: '模板管理', icon: FileArchive },
  { path: '/auth', label: '网站连接', icon: KeyRound },
  { path: '/notifications', label: '通知配置', icon: Bell },
  { path: '/settings', label: '系统设置', icon: Settings2 },
]
const activePath = computed(() => {
  if (route.path.startsWith('/jobs')) return '/jobs'
  if (route.path.startsWith('/runs')) return '/runs'
  if (route.path.startsWith('/notifications')) return '/notifications'
  return route.path
})
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar" :class="{ 'sidebar-open': menuOpen }">
      <div class="brand">
        <span class="brand-symbol">
          <img :src="LOGO_DATA_URL" alt="Logo" class="brand-logo" />
        </span>
        <span class="brand-type"><strong>{{ appName }}</strong></span>
        <button class="icon-button mobile-only" aria-label="关闭导航" @click="menuOpen = false"><X :size="18" /></button>
      </div>
      <div class="nav-heading">工作台</div>
      <nav class="side-nav" aria-label="主导航">
        <RouterLink v-for="item in navigation" :key="item.path" :to="item.path" class="side-link" :class="{ active: activePath === item.path }">
          <component :is="item.icon" :size="18" />
          <span>{{ item.label }}</span>
        </RouterLink>
      </nav>
      <div class="sidebar-bottom">
        <button class="side-link logout-link" @click="logout"><LogOut :size="18" /><span>退出登录</span></button>
      </div>
    </aside>
    <div v-if="menuOpen" class="mobile-scrim" @click="menuOpen = false"></div>
    <div class="main-frame">
      <header class="top-header">
        <button class="icon-button mobile-only" aria-label="打开导航" @click="menuOpen = true"><Menu :size="22" /></button>
        <div class="top-header-label"><span class="breadcrumb-root">工作台</span><span class="breadcrumb-separator">/</span><span>{{ navigation.find(item => item.path === activePath)?.label || '巡检任务' }}</span></div>
      </header>
      <main id="main-content" class="main-content"><Transition name="page" mode="out-in"><div :key="route.fullPath"><RouterView /></div></Transition></main>
    </div>
    <Transition name="toast"><div v-if="notice.message" class="notice" :class="`notice-${notice.kind}`" role="status">
      <CheckCircle2 v-if="notice.kind === 'success'" :size="19" /><AlertCircle v-else-if="notice.kind === 'error'" :size="19" /><Info v-else :size="19" />
      <span>{{ notice.message }}</span><button aria-label="关闭提示" @click="notice.message = ''"><X :size="16" /></button>
    </div></Transition>
    <Transition name="modal-fade">
      <div v-if="confirmState.open" class="modal-backdrop" @click="handleConfirm(false)">
        <div class="confirm-modal-box" role="dialog" aria-modal="true" aria-labelledby="confirm-title" aria-describedby="confirm-description" @click.stop>
          <div class="confirm-modal-head">
            <div class="confirm-icon-wrap" :class="{ danger: confirmState.danger }">
              <AlertCircle :size="22" />
            </div>
            <div class="confirm-modal-content">
              <h3 id="confirm-title">{{ confirmState.title }}</h3>
              <p id="confirm-description">{{ confirmState.message }}</p>
            </div>
          </div>
          <ButtonGroup class="confirm-modal-actions">
            <ActionButton type="button" @click="handleConfirm(false)" variant="secondary">
              {{ confirmState.cancelText }}
            </ActionButton>
            <ActionButton
              ref="confirmButton"

              :variant="confirmState.danger ? 'danger' : 'primary'"
              type="button"
              @click="handleConfirm(true)"
             >
              {{ confirmState.confirmText }}
            </ActionButton>
          </ButtonGroup>
        </div>
      </div>
    </Transition>
  </div>
</template>
