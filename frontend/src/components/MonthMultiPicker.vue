<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { CalendarDays, ChevronDown, ChevronLeft, ChevronRight, X } from '../icons'

const props = defineProps<{ modelValue: string[]; label: string }>()
const emit = defineEmits<{ 'update:modelValue': [value: string[]] }>()
const limit = 12
const currentParts = Object.fromEntries(new Intl.DateTimeFormat('en-US', {
  timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit',
}).formatToParts(new Date()).map(part => [part.type, part.value]))
const currentMonth = `${currentParts.year}-${currentParts.month}`
const open = ref(false)
const year = ref(Number(currentParts.year))
const trigger = ref<HTMLElement | null>(null)
const panel = ref<HTMLElement | null>(null)
const position = ref({ left: '0px', top: '0px' })
const limitMessage = ref('')
const selected = computed(() => [...props.modelValue].sort())
const displayValue = computed(() => {
  if (!selected.value.length) return '选择月份'
  if (selected.value.length === 2 && selected.value[0].slice(0, 4) === selected.value[1].slice(0, 4)) {
    const [firstYear, firstMonth] = selected.value[0].split('-')
    const secondMonth = Number(selected.value[1].slice(5, 7))
    return `${firstYear}年${Number(firstMonth)}月、${secondMonth}月`
  }
  if (selected.value.length > 1) return `已选 ${selected.value.length} 个月`
  const [valueYear, valueMonth] = selected.value[0].split('-')
  return `${valueYear}年${Number(valueMonth)}月`
})

function monthValue(month: number) {
  return `${String(year.value).padStart(4, '0')}-${String(month).padStart(2, '0')}`
}

function monthLabel(value: string) {
  const [valueYear, valueMonth] = value.split('-')
  return `${valueYear}年${Number(valueMonth)}月`
}

function close(focus = false) {
  open.value = false
  if (focus) trigger.value?.focus()
}

async function toggle() {
  if (open.value) { close(); return }
  year.value = Number((selected.value.at(-1) || currentMonth).slice(0, 4))
  limitMessage.value = ''
  open.value = true
  await nextTick()
  const target = trigger.value?.getBoundingClientRect()
  const popup = panel.value?.getBoundingClientRect()
  if (!target || !popup) return
  const left = Math.max(12, Math.min(target.left, window.innerWidth - popup.width - 12))
  const below = target.bottom + 8
  const above = target.top - popup.height - 8
  const top = below + popup.height <= window.innerHeight - 12 || above < 12 ? below : above
  position.value = { left: `${left}px`, top: `${Math.max(12, top)}px` }
}

function toggleMonth(month: number) {
  const value = monthValue(month)
  if (selected.value.includes(value)) {
    emit('update:modelValue', selected.value.filter(item => item !== value))
  } else if (selected.value.length < limit) {
    emit('update:modelValue', [...selected.value, value].sort())
  } else {
    limitMessage.value = `一次最多选择 ${limit} 个月`
    return
  }
  limitMessage.value = ''
}

function removeMonth(value: string) {
  emit('update:modelValue', selected.value.filter(item => item !== value))
  limitMessage.value = ''
}

function pointerDown(event: PointerEvent) {
  const target = event.target as Node
  if (open.value && !trigger.value?.contains(target) && !panel.value?.contains(target)) close()
}

function keyDown(event: KeyboardEvent) {
  if (open.value && event.key === 'Escape') { event.preventDefault(); close(true) }
}

function closeOnScroll(event: Event) {
  if (open.value && (!(event.target instanceof Node) || !panel.value?.contains(event.target))) close()
}

onMounted(() => {
  document.addEventListener('pointerdown', pointerDown)
  document.addEventListener('keydown', keyDown)
  window.addEventListener('resize', closeOnScroll)
  window.addEventListener('scroll', closeOnScroll, true)
})
onUnmounted(() => {
  document.removeEventListener('pointerdown', pointerDown)
  document.removeEventListener('keydown', keyDown)
  window.removeEventListener('resize', closeOnScroll)
  window.removeEventListener('scroll', closeOnScroll, true)
})
</script>

<template>
  <button ref="trigger" type="button" class="month-multi-trigger" :class="{ open }"
    :aria-label="`${label}：${displayValue}`" :title="selected.map(monthLabel).join('、')"
    aria-haspopup="dialog" :aria-expanded="open" @click="toggle">
    <span>{{ displayValue }}</span><CalendarDays :size="15" aria-hidden="true" /><ChevronDown :size="13" aria-hidden="true" />
  </button>
  <Teleport to="body">
    <div v-if="open" ref="panel" class="month-multi-panel" :style="position" role="dialog" :aria-label="label">
      <div class="month-multi-heading">
        <button type="button" aria-label="上一年" :disabled="year <= 1" @click="year--"><ChevronLeft :size="17" /></button>
        <strong>{{ year }}年</strong>
        <button type="button" aria-label="下一年" :disabled="year >= 9999" @click="year++"><ChevronRight :size="17" /></button>
      </div>
      <div class="month-multi-grid">
        <button v-for="month in 12" :key="month" type="button" :class="{ selected: selected.includes(monthValue(month)) }"
          :aria-label="`${year}年${month}月`" :aria-pressed="selected.includes(monthValue(month))"
          @click="toggleMonth(month)">{{ month }}月</button>
      </div>
      <div v-if="selected.length" class="month-multi-selected" aria-label="已选月份">
        <button v-for="value in selected" :key="value" type="button" :aria-label="`取消${monthLabel(value)}`" @click="removeMonth(value)">{{ monthLabel(value) }}<X :size="12" aria-hidden="true" /></button>
      </div>
      <p v-if="limitMessage" class="month-multi-error" role="alert">{{ limitMessage }}</p>
      <div class="month-multi-footer">
        <span>已选 {{ selected.length }} / {{ limit }}</span>
        <button type="button" class="month-multi-clear" @click="emit('update:modelValue', []); limitMessage = ''">清空</button>
        <button type="button" class="month-multi-done" @click="close(true)">完成</button>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.month-multi-trigger{box-sizing:border-box;display:flex;align-items:center;gap:8px;width:204px;height:34px;padding:0 10px;border:1px solid rgba(164,197,209,.7);border-radius:6px;background:rgba(255,255,255,.74);color:#263e4c;font-family:inherit;font-size:12px;font-weight:600;cursor:pointer}
.month-multi-trigger span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.month-multi-trigger svg{flex:none;color:#6d8996}.month-multi-trigger svg:last-child{margin-left:auto}
.month-multi-trigger:hover,.month-multi-trigger.open{border-color:#83b4c3;background:#fbfeff}.month-multi-trigger.open{box-shadow:0 0 0 3px rgba(36,122,147,.09)}
.month-multi-trigger:focus-visible,.month-multi-panel button:focus-visible{outline:2px solid #2285a1;outline-offset:2px}
.month-multi-panel{box-sizing:border-box;position:fixed;z-index:1200;width:306px;max-height:calc(100vh - 24px);overflow:auto;padding:12px;border:1px solid #ccdde5;border-radius:10px;background:rgba(248,253,255,.98);backdrop-filter:blur(14px);box-shadow:0 14px 38px rgba(28,63,80,.17);color:#314d5b;font-family:inherit}
.month-multi-heading{display:grid;grid-template-columns:30px 1fr 30px;align-items:center;margin-bottom:8px;text-align:center;font-size:13px}
.month-multi-heading button{display:grid;place-items:center;width:30px;height:30px;border:0;border-radius:6px;background:transparent;color:#31596a;cursor:pointer}.month-multi-heading button:hover:not(:disabled){background:#edf6f8}.month-multi-heading button:disabled{opacity:.35;cursor:default}
.month-multi-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:5px}
.month-multi-grid button{height:34px;border:0;border-radius:6px;background:transparent;color:#365766;font-family:inherit;font-size:12px;cursor:pointer}.month-multi-grid button:hover:not(.selected){background:#edf6f8;color:#176b84}.month-multi-grid button.selected{background:#176b84;color:#fff;font-weight:700}
.month-multi-selected{display:flex;flex-wrap:wrap;gap:5px;margin-top:10px;padding-top:9px;border-top:1px solid #e7f0f3}
.month-multi-selected button{display:inline-flex;align-items:center;gap:4px;min-height:25px;padding:3px 7px;border:1px solid #c5dce4;border-radius:5px;background:#eef7fa;color:#27677c;font-family:inherit;font-size:11px;cursor:pointer}.month-multi-selected button:hover{border-color:#8db9c7;background:#e3f2f6}
.month-multi-error{margin:8px 0 0;color:#b7484c;font-size:11px}
.month-multi-footer{display:flex;align-items:center;gap:8px;margin-top:10px;padding-top:9px;border-top:1px solid #e7f0f3;font-size:11px;color:#8397a1}
.month-multi-footer button{height:27px;padding:0 9px;border:0;border-radius:5px;font-family:inherit;font-size:11px;font-weight:700;cursor:pointer}.month-multi-clear{margin-left:auto;background:transparent;color:#47798a}.month-multi-clear:hover{background:#e9f3f6}.month-multi-done{background:#176b84;color:#fff}.month-multi-done:hover{background:#10566d}
</style>
