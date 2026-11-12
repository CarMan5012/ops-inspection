<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { CalendarDays, ChevronLeft, ChevronRight, ChevronDown } from '../icons'

const props = defineProps<{
  modelValue: string
  mode: 'month' | 'date'
  label: string
}>()
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()

const nowParts = Object.fromEntries(new Intl.DateTimeFormat('en-US', {
  timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
}).formatToParts(new Date()).map(part => [part.type, part.value]))
const today = `${nowParts.year}-${nowParts.month}-${nowParts.day}`
const open = ref(false)
const trigger = ref<HTMLElement | null>(null)
const panel = ref<HTMLElement | null>(null)
const viewYear = ref(Number(today.slice(0, 4)))
const viewMonth = ref(Number(today.slice(5, 7)))
const showMonths = ref(props.mode === 'month')
const panelPosition = ref({ left: '0px', top: '0px' })

const displayValue = computed(() => {
  const parts = props.modelValue.split('-').map(Number)
  if (!parts[0] || !parts[1]) return props.mode === 'month' ? '选择月份' : '选择日期'
  return props.mode === 'month'
    ? `${parts[0]}年${parts[1]}月`
    : parts[2] ? `${parts[0]}年${parts[1]}月${parts[2]}日` : '选择日期'
})

const days = computed(() => {
  const first = new Date(0)
  first.setUTCFullYear(viewYear.value, viewMonth.value - 1, 1)
  const offset = (first.getUTCDay() + 6) % 7
  const last = new Date(0)
  last.setUTCFullYear(viewYear.value, viewMonth.value, 0)
  const count = Math.ceil((offset + last.getUTCDate()) / 7) * 7
  return Array.from({ length: count }, (_, index) => {
    const current = new Date(0)
    current.setUTCFullYear(viewYear.value, viewMonth.value - 1, index - offset + 1)
    const year = current.getUTCFullYear()
    const month = current.getUTCMonth() + 1
    const day = current.getUTCDate()
    return {
      value: `${String(year).padStart(4, '0')}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`,
      day,
      inMonth: month === viewMonth.value,
    }
  })
})

function close(focus = false) {
  open.value = false
  if (focus) trigger.value?.focus()
}

async function toggle() {
  if (open.value) { close(); return }
  const current = /^\d{4}-(0[1-9]|1[0-2])/.test(props.modelValue) ? props.modelValue : today
  viewYear.value = Number(current.slice(0, 4))
  viewMonth.value = Number(current.slice(5, 7))
  showMonths.value = props.mode === 'month'
  open.value = true
  await nextTick()
  const rect = trigger.value?.getBoundingClientRect()
  const popup = panel.value?.getBoundingClientRect()
  if (!rect || !popup) return
  const left = Math.max(12, Math.min(rect.left, window.innerWidth - popup.width - 12))
  const below = rect.bottom + 8
  const above = rect.top - popup.height - 8
  const top = below + popup.height <= window.innerHeight - 12 || above < 12 ? below : above
  panelPosition.value = { left: `${left}px`, top: `${Math.max(12, top)}px` }
}

function shift(direction: number) {
  if (showMonths.value) {
    viewYear.value = Math.max(1, Math.min(9999, viewYear.value + direction))
    return
  }
  const next = viewMonth.value + direction
  if (next < 1 && viewYear.value > 1) { viewYear.value--; viewMonth.value = 12 }
  else if (next > 12 && viewYear.value < 9999) { viewYear.value++; viewMonth.value = 1 }
  else if (next >= 1 && next <= 12) viewMonth.value = next
}

function chooseMonth(month: number) {
  viewMonth.value = month
  if (props.mode === 'month') {
    emit('update:modelValue', `${String(viewYear.value).padStart(4, '0')}-${String(month).padStart(2, '0')}`)
    close(true)
  } else showMonths.value = false
}

function chooseDay(value: string) {
  emit('update:modelValue', value)
  close(true)
}

function chooseCurrent() {
  emit('update:modelValue', props.mode === 'month' ? today.slice(0, 7) : today)
  close(true)
}

function pointerDown(event: PointerEvent) {
  const target = event.target as Node
  if (open.value && !trigger.value?.contains(target) && !panel.value?.contains(target)) close()
}

function keyDown(event: KeyboardEvent) {
  if (open.value && event.key === 'Escape') { event.preventDefault(); close(true) }
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
function closeOnScroll(event: Event) {
  if (open.value && (!(event.target instanceof Node) || !panel.value?.contains(event.target))) close()
}
</script>

<template>
  <button ref="trigger" type="button" class="period-trigger" :class="{ open }"
    :aria-label="`${label}：${displayValue}`" aria-haspopup="dialog" :aria-expanded="open" @click="toggle">
    <span>{{ displayValue }}</span><CalendarDays :size="15" aria-hidden="true" /><ChevronDown :size="13" aria-hidden="true" />
  </button>
  <Teleport to="body">
    <div v-if="open" ref="panel" class="period-panel" :style="panelPosition" role="dialog" :aria-label="label">
      <div class="period-panel-head">
        <button type="button" class="period-nav" :aria-label="showMonths ? '上一年' : '上个月'" @click="shift(-1)"><ChevronLeft :size="17" /></button>
        <button v-if="mode === 'date'" type="button" class="period-heading" :aria-label="'选择年月'" @click="showMonths = !showMonths">{{ viewYear }}年<span v-if="!showMonths">{{ viewMonth }}月</span><ChevronDown :size="13" /></button>
        <strong v-else class="period-heading-static">{{ viewYear }}年</strong>
        <button type="button" class="period-nav" :aria-label="showMonths ? '下一年' : '下个月'" @click="shift(1)"><ChevronRight :size="17" /></button>
      </div>
      <div v-if="showMonths" class="period-month-grid">
        <button v-for="month in 12" :key="month" type="button" class="period-cell"
          :class="{ selected: modelValue.startsWith(`${String(viewYear).padStart(4, '0')}-${String(month).padStart(2, '0')}`) }"
          :aria-label="`${viewYear}年${month}月`" :aria-pressed="modelValue.startsWith(`${String(viewYear).padStart(4, '0')}-${String(month).padStart(2, '0')}`)"
          @click="chooseMonth(month)">{{ month }}月</button>
      </div>
      <template v-else>
        <div class="period-weekdays"><span v-for="day in ['一', '二', '三', '四', '五', '六', '日']" :key="day">{{ day }}</span></div>
        <div class="period-day-grid">
          <button v-for="day in days" :key="day.value" type="button" class="period-cell"
            :class="{ muted: !day.inMonth, selected: modelValue === day.value, today: today === day.value }"
            :aria-label="day.value" :aria-pressed="modelValue === day.value" @click="chooseDay(day.value)">{{ day.day }}</button>
        </div>
      </template>
      <div class="period-panel-footer"><button type="button" @click="chooseCurrent">{{ mode === 'month' ? '本月' : '今天' }}</button></div>
    </div>
  </Teleport>
</template>

<style scoped>
.period-trigger{box-sizing:border-box;display:flex;align-items:center;justify-content:space-between;gap:8px;width:174px;height:34px;padding:0 10px;border:1px solid rgba(164,197,209,.7);border-radius:6px;background:rgba(255,255,255,.74);color:#263e4c;font-family:inherit;font-size:12px;font-weight:600;cursor:pointer}
.period-trigger span{white-space:nowrap}.period-trigger svg{flex:none;color:#6d8996}.period-trigger svg:last-child{margin-left:auto}
.period-trigger:hover,.period-trigger.open{border-color:#83b4c3;background:#fbfeff}.period-trigger.open{box-shadow:0 0 0 3px rgba(36,122,147,.09)}
.period-trigger:focus-visible,.period-panel button:focus-visible{outline:2px solid #2285a1;outline-offset:2px}
.period-panel{position:fixed;z-index:1200;width:286px;max-height:calc(100vh - 24px);overflow:auto;padding:12px;border:1px solid #ccdde5;border-radius:10px;background:rgba(248,253,255,.97);backdrop-filter:blur(14px);box-shadow:0 14px 38px rgba(28,63,80,.17);color:#314d5b;font-family:inherit}
.period-panel-head{display:grid;grid-template-columns:30px 1fr 30px;align-items:center;margin-bottom:8px}
.period-nav,.period-heading{display:inline-flex;align-items:center;justify-content:center;border:0;background:transparent;color:#31596a;cursor:pointer}
.period-nav{width:30px;height:30px;border-radius:6px}.period-nav:hover,.period-heading:hover{background:#edf6f8}
.period-heading,.period-heading-static{min-height:30px;gap:4px;border-radius:6px;font-size:13px;font-weight:700}.period-heading-static{display:grid;place-items:center}
.period-month-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:5px}
.period-weekdays,.period-day-grid{display:grid;grid-template-columns:repeat(7,1fr);gap:3px;text-align:center}
.period-weekdays{margin:2px 0 5px;color:#92a5ae;font-size:11px}.period-weekdays span{height:25px;display:grid;place-items:center}
.period-cell{height:34px;border:0;border-radius:6px;background:transparent;color:#365766;font-family:inherit;font-size:12px;cursor:pointer}
.period-cell:hover:not(.selected){background:#edf6f8;color:#176b84}.period-cell.selected{background:#176b84;color:#fff;font-weight:700}.period-cell.muted{color:#b6c3c9}.period-cell.today:not(.selected){box-shadow:inset 0 0 0 1px #8fbfca}
.period-panel-footer{display:flex;justify-content:flex-end;margin-top:9px;padding-top:8px;border-top:1px solid #edf2f4}
.period-panel-footer button{padding:4px 7px;border:0;border-radius:5px;background:transparent;color:#176b84;font-family:inherit;font-size:12px;font-weight:700;cursor:pointer}.period-panel-footer button:hover{background:#edf6f8}
</style>
