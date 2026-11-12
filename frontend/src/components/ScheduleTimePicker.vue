<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { ChevronDown } from '../icons'

const props = defineProps<{ modelValue: string; label: string; minHour?: number; maxHour?: number; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
const root = ref<HTMLElement | null>(null)
const hourColumn = ref<HTMLElement | null>(null)
const minuteColumn = ref<HTMLElement | null>(null)
const open = ref(false)
const hours = computed(() => Array.from({ length: 24 }, (_, index) => index)
  .filter(index => index >= (props.minHour ?? 0) && index <= (props.maxHour ?? 23))
  .map(index => String(index).padStart(2, '0')))
const minutes = Array.from({ length: 60 }, (_, index) => String(index).padStart(2, '0'))
const hour = computed(() => String(props.modelValue || '09:00').slice(0, 2))
const minute = computed(() => String(props.modelValue || '09:00').slice(3, 5))
const random = computed(() => props.modelValue.endsWith(':R'))

async function toggle() {
  if (props.disabled) return
  open.value = !open.value
  if (!open.value) return
  await nextTick()
  for (const [column, value] of [[hourColumn.value, hour.value], [minuteColumn.value, minute.value]] as const) {
    const selected = column?.querySelector<HTMLElement>(`[data-value="${value}"]`)
    if (column && selected) column.scrollTop = selected.offsetTop - column.offsetTop - column.clientHeight / 2 + selected.clientHeight / 2
  }
}
function chooseHour(value: string) {
  emit('update:modelValue', `${value}:${random.value ? 'R' : minute.value}`)
  if (random.value) open.value = false
}
function chooseMinute(value: string) {
  emit('update:modelValue', `${hour.value}:${value}`)
  open.value = false
}
function onOutside(event: PointerEvent) {
  if (root.value && !root.value.contains(event.target as Node)) open.value = false
}
function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && open.value) {
    open.value = false
    root.value?.querySelector<HTMLElement>('.schedule-time-trigger')?.focus()
  }
}
onMounted(() => { document.addEventListener('pointerdown', onOutside); document.addEventListener('keydown', onKeydown) })
onUnmounted(() => { document.removeEventListener('pointerdown', onOutside); document.removeEventListener('keydown', onKeydown) })
</script>

<template>
  <div ref="root" class="schedule-time-picker">
    <button type="button" class="schedule-time-trigger" :aria-label="label" :disabled="disabled" aria-haspopup="dialog" :aria-expanded="open" @click="toggle">
      <span class="schedule-time-number">{{ hour }}<template v-if="!random">:{{ minute }}</template></span>
      <span v-if="random" class="schedule-time-random">点 · 分钟随机</span>
      <ChevronDown :size="15" aria-hidden="true" />
    </button>
    <div v-if="open" class="schedule-time-popover" role="dialog" :aria-label="label">
      <div class="schedule-time-column-wrap">
        <div class="schedule-time-column-group"><span>小时</span><div ref="hourColumn" class="schedule-time-column" aria-label="小时">
          <button v-for="value in hours" :key="value" type="button" :aria-label="`${value} 点`" :aria-pressed="hour === value" :data-value="value" :class="{ selected: hour === value }" @click="chooseHour(value)">{{ value }}</button>
        </div></div>
        <div v-if="!random" class="schedule-time-column-group"><span>分钟</span><div ref="minuteColumn" class="schedule-time-column" aria-label="分钟">
          <button v-for="value in minutes" :key="value" type="button" :aria-label="`${value} 分`" :aria-pressed="minute === value" :data-value="value" :class="{ selected: minute === value }" @click="chooseMinute(value)">{{ value }}</button>
        </div></div>
      </div>
    </div>
  </div>
</template>
