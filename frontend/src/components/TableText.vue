<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useAttrs, useId, watch } from 'vue'

defineOptions({ inheritAttrs: false })
const props = withDefaults(defineProps<{ text?: string | number | null; interactive?: boolean }>(), { interactive: true })
const attrs = useAttrs()
const text = computed(() => props.text == null || props.text === '' ? '—' : String(props.text))
const trigger = ref<HTMLElement | null>(null)
const panel = ref<HTMLElement | null>(null)
const overflowing = ref(false)
const position = ref<{ left: number; top: number; width: number; maxHeight: number } | null>(null)
const pinned = ref(false)
const id = useId()
let observer: ResizeObserver | undefined
let timer: ReturnType<typeof setTimeout> | undefined
function measure() {
  overflowing.value = Boolean(trigger.value && trigger.value.scrollWidth > trigger.value.clientWidth + 1)
  if (!overflowing.value) close()
}
function open() {
  clearTimeout(timer)
  if (!overflowing.value || !trigger.value) return
  const rect = trigger.value.getBoundingClientRect()
  const width = Math.min(420, window.innerWidth - 24)
  const below = window.innerHeight - rect.bottom - 12
  const top = Math.max(12, Math.min(window.innerHeight - 84,
    below >= 150 || rect.top < 150 ? rect.bottom + 8 : rect.top - 240))
  position.value = { left: Math.max(12, Math.min(rect.left, window.innerWidth - width - 12)), top, width,
    maxHeight: Math.min(220, window.innerHeight - top - 12) }
}
function keepOpen() { clearTimeout(timer) }
function close() { clearTimeout(timer); position.value = null; pinned.value = false }
function leave() { if (!pinned.value) timer = setTimeout(close, 140) }
function toggle(event: Event) {
  if (!overflowing.value || !props.interactive) return
  event.preventDefault()
  event.stopPropagation()
  if (pinned.value) close()
  else { open(); pinned.value = true }
}
function outside(event: Event) {
  const node = event.target as Node
  if (!trigger.value?.contains(node) && !panel.value?.contains(node)) close()
}
function onScroll(event: Event) {
  if (panel.value?.contains(event.target as Node)) return
  if (position.value && document.activeElement === trigger.value && trigger.value) {
    const rect = trigger.value.getBoundingClientRect()
    if (rect.bottom > 0 && rect.top < window.innerHeight && rect.right > 0 && rect.left < window.innerWidth) {
      open()
      return
    }
  }
  close()
}
function resize() { close(); measure() }
function key(event: KeyboardEvent) { if (event.key === 'Escape') close() }
watch(text, async () => { close(); await nextTick(); measure() })
onMounted(() => {
  observer = new ResizeObserver(measure)
  if (trigger.value) observer.observe(trigger.value)
  measure()
  document.addEventListener('pointerdown', outside)
  window.addEventListener('scroll', onScroll, true)
  window.addEventListener('resize', resize)
  window.addEventListener('keydown', key)
})
onBeforeUnmount(() => {
  observer?.disconnect(); clearTimeout(timer)
  document.removeEventListener('pointerdown', outside)
  window.removeEventListener('scroll', onScroll, true)
  window.removeEventListener('resize', resize)
  window.removeEventListener('keydown', key)
})
</script>

<template>
  <span ref="trigger" v-bind="attrs" class="table-text" :class="{ 'can-expand': overflowing && interactive }"
    :role="overflowing && interactive ? 'button' : undefined" :tabindex="overflowing && interactive ? 0 : undefined"
    :aria-expanded="overflowing && interactive ? Boolean(position) : undefined"
    :aria-describedby="position ? id : undefined" @mouseenter="open" @mouseleave="leave"
    @focus="open" @blur="leave" @click="toggle" @keydown.enter="toggle" @keydown.space="toggle">
    {{ text }}
  </span>
  <Teleport to="body">
    <div v-if="position" :id="id" ref="panel" role="tooltip" class="table-text-detail" :style="{
      left: `${position.left}px`, top: `${position.top}px`, width: `${position.width}px`,
      maxHeight: `${position.maxHeight}px`,
    }" @mouseenter="keepOpen" @mouseleave="leave">{{ text }}</div>
  </Teleport>
</template>
