<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { api } from '../api'
import { ExternalLink } from '../icons'

type Choice = { label: string; value: string }
type Variable = {
  name: string; label: string; type: string; multi: boolean
  options: Choice[]; default: string[]; dynamic: boolean
}
type Settings = {
  uid: string; title: string; variables: Variable[]
  time: { from: string; to: string }; refresh: string
  time_choices: { label: string; from: string; to: string }[]; refresh_choices: string[]
}

const props = defineProps<{ url: string; authProfileId: number | string }>()
const emit = defineEmits<{ 'update:url': [value: string] }>()
const settings = ref<Settings | null>(null)
const optionsRoot = ref<HTMLElement | null>(null)
const loading = ref(false)
const error = ref('')
const importUrl = ref('')
const values = reactive<Record<string, string[]>>({})
const timePreset = ref('dashboard')
const customFrom = ref('now-1h')
const customTo = ref('now')
const refresh = ref('dashboard')
let requestId = 0

function closeMultiMenus(event: PointerEvent): void {
  const target = event.target
  if (!(target instanceof Node)) return
  optionsRoot.value?.querySelectorAll<HTMLDetailsElement>('.grafana-multi-dropdown[open]').forEach(menu => {
    if (!menu.contains(target)) menu.open = false
  })
}

function closeMultiMenusOnEscape(event: KeyboardEvent): void {
  if (event.key !== 'Escape') return
  optionsRoot.value?.querySelectorAll<HTMLDetailsElement>('.grafana-multi-dropdown[open]').forEach(menu => {
    menu.open = false
  })
}

onMounted(() => {
  document.addEventListener('pointerdown', closeMultiMenus)
  document.addEventListener('keydown', closeMultiMenusOnEscape)
})
onBeforeUnmount(() => {
  requestId++
  document.removeEventListener('pointerdown', closeMultiMenus)
  document.removeEventListener('keydown', closeMultiMenusOnEscape)
})

const uid = computed(() => {
  try { return new URL(props.url).pathname.match(/\/d(?:-solo)?\/([^/]+)/)?.[1] || '' }
  catch { return '' }
})
const dashboardKey = computed(() => `${props.authProfileId}:${uid.value}`)
const timeChoices = computed(() => (settings.value?.time_choices || []).map(choice => ({
  value: `${choice.from}|${choice.to}`, label: choice.label, from: choice.from, to: choice.to,
})))
const refreshChoices = computed(() => (settings.value?.refresh_choices || []).map(value => ({
  value, label: value === 'off' ? '关闭' : value === 'auto' ? '自动' : value,
})))
const defaultTimeLabel = computed(() => {
  if (!settings.value) return '看板默认'
  const { from, to } = settings.value.time
  const relative = to === 'now' ? /^now-(\d+)([smhdwMy])$/.exec(from) : null
  const units: Record<string, string> = { s: '秒', m: '分钟', h: '小时', d: '天', w: '周', M: '个月', y: '年' }
  const label = timeChoices.value.find(choice => choice.from === from && choice.to === to)?.label
    || (relative ? `最近 ${relative[1]} ${units[relative[2]]}` : `${from} 至 ${to}`)
  return `默认 · ${label}`
})
const defaultRefreshLabel = computed(() => {
  const saved = settings.value?.refresh || 'off'
  return `默认 · ${saved === 'off' ? '关闭' : refreshChoices.value.find(choice => choice.value === saved)?.label || saved}`
})

function selected(variable: Variable): string[] {
  return values[variable.name] || []
}

function syncFromUrl(): void {
  if (!settings.value) return
  const url = new URL(props.url)
  for (const variable of settings.value.variables) {
    const passed = url.searchParams.getAll(`var-${variable.name}`)
    const current = passed.length ? passed : variable.default
    values[variable.name] = variable.multi ? [...new Set(current)] : current.slice(0, 1)
  }
  const from = url.searchParams.get('from')
  const to = url.searchParams.get('to')
  customFrom.value = from || settings.value.time.from
  customTo.value = to || settings.value.time.to
  timePreset.value = !from && !to ? 'dashboard'
    : timeChoices.value.find(choice => choice.from === from && choice.to === to)?.value || 'custom'
  const savedRefresh = url.searchParams.get('refresh')
  refresh.value = savedRefresh || 'dashboard'
}

function updateUrl(): void {
  if (!settings.value) return
  const url = new URL(props.url)
  for (const variable of settings.value.variables) url.searchParams.delete(`var-${variable.name}`)
  for (const variable of settings.value.variables) {
    for (const value of selected(variable)) url.searchParams.append(`var-${variable.name}`, value)
  }
  url.searchParams.delete('from')
  url.searchParams.delete('to')
  if (timePreset.value === 'custom') {
    if (customFrom.value.trim()) url.searchParams.set('from', customFrom.value.trim())
    if (customTo.value.trim()) url.searchParams.set('to', customTo.value.trim())
  } else if (timePreset.value !== 'dashboard') {
    const chosen = timeChoices.value.find(choice => choice.value === timePreset.value)
    if (chosen) {
      url.searchParams.set('from', chosen.from)
      url.searchParams.set('to', chosen.to)
    }
  }
  url.searchParams.delete('refresh')
  if (refresh.value !== 'dashboard') url.searchParams.set('refresh', refresh.value)
  emit('update:url', url.toString())
}

function setSingle(name: string, value: string): void {
  values[name] = value ? [value] : []
  updateUrl()
}

function selectedSummary(variable: Variable): string {
  const chosen = selected(variable)
  if (!chosen.length) return '请选择'
  const labels = chosen.map(value => variable.options.find(option => option.value === value)?.label || value)
  return labels.length > 2 ? `${labels.slice(0, 2).join('、')} +${labels.length - 2}` : labels.join('、')
}

function setMulti(name: string, value: string, checked: boolean): void {
  const current = values[name] || []
  values[name] = checked
    ? value === '$__all' ? ['$__all'] : [...new Set([...current.filter(item => item !== '$__all'), value])]
    : current.filter(item => item !== value)
  updateUrl()
}

function importCurrentUrl(): void {
  if (!settings.value) return
  try {
    const pasted = new URL(importUrl.value.trim())
    const current = new URL(props.url)
    const pastedUid = pasted.pathname.match(/\/d(?:-solo)?\/([^/]+)/)?.[1]
    if (pasted.origin !== current.origin || pastedUid !== settings.value.uid || pasted.pathname !== current.pathname) {
      throw new Error('请粘贴当前看板的链接')
    }
    emit('update:url', pasted.toString())
    importUrl.value = ''
    error.value = ''
    // The key stays the same, so update the controls from the imported link directly.
    void nextTick(syncFromUrl)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '链接格式不正确'
  }
}

watch([dashboardKey, () => props.url], async ([key], previous) => {
  const currentRequest = ++requestId
  if (!previous || key !== previous[0]) settings.value = null
  error.value = ''
  if (!uid.value || !props.authProfileId) {
    loading.value = false
    return
  }
  loading.value = true
  try {
    const params = new URL(props.url).search
    const result = await api<Settings>(`/auth-profiles/${props.authProfileId}/dashboards/${encodeURIComponent(uid.value)}/settings?params=${encodeURIComponent(params)}`)
    if (currentRequest !== requestId) return
    settings.value = result
    syncFromUrl()
  } catch (cause) {
    if (currentRequest === requestId) error.value = cause instanceof Error ? cause.message : '读取看板选项失败'
  } finally {
    if (currentRequest === requestId) loading.value = false
  }
}, { immediate: true })
</script>

<template>
  <section ref="optionsRoot" class="grafana-options" aria-label="看板显示设置">
    <div class="grafana-options-head"><div><strong>看板显示</strong><p>与当前 Grafana 看板同步</p></div><a :href="url" target="_blank" rel="noopener noreferrer">在 Grafana 中打开 <ExternalLink :size="14" /></a></div>
    <p v-if="loading" class="grafana-options-loading" role="status"><span class="grafana-loading-spinner" aria-hidden="true"></span>正在读取看板选项…</p>
    <template v-if="settings">
      <div class="grafana-variable-grid">
        <div v-for="variable in settings.variables" :key="variable.name" class="grafana-variable">
          <span class="grafana-variable-label">{{ variable.label }}</span>
          <input v-if="variable.type === 'textbox'" :id="`grafana-var-${variable.name}`" :aria-label="variable.label" :value="values[variable.name]?.[0] || ''" placeholder="输入 Grafana 文本变量" @change="setSingle(variable.name, ($event.target as HTMLInputElement).value.trim())" />
          <template v-else-if="variable.multi">
            <details class="grafana-multi-dropdown">
              <summary :id="`grafana-var-${variable.name}`" :aria-label="`${variable.label}：${selectedSummary(variable)}`">{{ selectedSummary(variable) }}</summary>
              <div class="grafana-multi-menu">
                <label v-for="option in variable.options" :key="option.value" class="grafana-variable-choice">
                  <input type="checkbox" :checked="(values[variable.name] || []).includes(option.value)" @change="setMulti(variable.name, option.value, ($event.target as HTMLInputElement).checked)" />{{ option.label }}
                </label>
                <label v-for="value in selected(variable).filter(value => !variable.options.some(option => option.value === value))" :key="value" class="grafana-variable-choice">
                  <input type="checkbox" checked @change="setMulti(variable.name, value, false)" />{{ value }}（当前链接）
                </label>
              </div>
            </details>
          </template>
          <template v-else>
            <div class="grafana-select-wrap"><select :id="`grafana-var-${variable.name}`" :aria-label="variable.label" :value="values[variable.name]?.[0] || ''" @change="setSingle(variable.name, ($event.target as HTMLSelectElement).value)"><option value="">请选择</option><option v-if="values[variable.name]?.[0] && !variable.options.some(option => option.value === values[variable.name]?.[0])" :value="values[variable.name][0]">{{ values[variable.name][0] }}（当前链接）</option><option v-for="option in variable.options" :key="option.value" :value="option.value">{{ option.label }}</option></select></div>
          </template>
        </div>
        <label class="grafana-variable grafana-time-variable"><span class="grafana-variable-label">时间范围</span><span class="grafana-select-wrap"><select v-model="timePreset" @change="updateUrl"><option value="dashboard">{{ defaultTimeLabel }}</option><optgroup label="Grafana 看板时间范围"><option v-for="choice in timeChoices" :key="choice.value" :value="choice.value">{{ choice.label }}</option><option value="custom">自定义时间</option></optgroup></select></span></label>
        <label class="grafana-variable grafana-refresh-variable"><span class="grafana-variable-label">自动刷新</span><span class="grafana-select-wrap"><select v-model="refresh" @change="updateUrl"><option value="dashboard">{{ defaultRefreshLabel }}</option><optgroup label="Grafana 看板刷新间隔"><option v-for="choice in refreshChoices" :key="choice.value" :value="choice.value">{{ choice.label }}</option><option v-if="!refreshChoices.some(choice => choice.value === refresh) && refresh !== 'dashboard'" :value="refresh">{{ refresh }}</option></optgroup></select></span></label>
        <template v-if="timePreset === 'custom'"><label class="grafana-variable grafana-custom-time"><span class="grafana-variable-label">开始</span><input v-model="customFrom" placeholder="例如 now-2h" @change="updateUrl" /></label><label class="grafana-variable grafana-custom-time"><span class="grafana-variable-label">结束</span><input v-model="customTo" placeholder="例如 now" @change="updateUrl" /></label></template>
      </div>
      <div class="grafana-options-foot"><p class="grafana-options-note">截图按以上条件执行，自动刷新会在截图前暂停。</p><details class="grafana-import"><summary>使用 Grafana 当前链接</summary><p>在 Grafana 中选好条件后，复制浏览器地址并粘贴到这里。</p><div><input v-model="importUrl" type="url" placeholder="粘贴当前看板链接" /><ActionButton @click="importCurrentUrl">使用链接</ActionButton></div></details></div>
    </template>
    <p v-if="error" class="dashboard-selection-error" role="alert">{{ error }}</p>
  </section>
</template>
