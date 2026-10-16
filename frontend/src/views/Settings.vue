<script setup lang="ts">
import { onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import QRCode from 'qrcode'
import { Database, ShieldCheck, RefreshCw, HardDrive, X, ChevronLeft, ChevronRight } from '../icons'
import { api, type Row } from '../api'
import { attempt, notify, confirmModal } from '../ui'
import RefreshButton from '../components/RefreshButton.vue'

const usage = ref<Row>({})
const route = useRoute()
const storage = reactive<Row>({})
const security = reactive<Row>({})
const swagger = ref(false)
const busy = ref(false)
const pendingAction = ref<'storage' | 'security' | 'cleanup' | null>(null)
const refreshing = ref(false)
const cleanupMonth = ref('')
const cleanupScopes = ref<string[]>([])
const cleanupPeriod = ref('')
const cleanupMenuOpen = ref(false)
const cleanupMenuRef = ref<HTMLElement | null>(null)
const cleanupMonthMenuOpen = ref(false)
const cleanupMonthMenuRef = ref<HTMLElement | null>(null)
const cleanupMonthYear = ref(new Date().getFullYear())
const cleanupPreview = ref<Row | null>(null)
const cleanupPreviewBusy = ref(false)
const cleanupScopeOptions = [
  { value: 'inspection', label: '巡检数据', detail: '截图、Word 报告和运行记录' },
  { value: 'periodic', label: '周期报告', detail: '压缩包和生成记录' },
  { value: 'logs', label: '日志文件', detail: '系统运行日志' },
]
const cleanupPeriodOptions = [
  { value: '7', label: '7 天前' },
  { value: '30', label: '30 天前' },
  { value: '90', label: '90 天前' },
  { value: '180', label: '180 天前' },
  { value: '365', label: '1 年前' },
  { value: 'month', label: '指定月份' },
]
const previousMonthDate = new Date()
previousMonthDate.setDate(1)
previousMonthDate.setMonth(previousMonthDate.getMonth() - 1)
const latestCompletedMonth = ref(`${previousMonthDate.getFullYear()}-${String(previousMonthDate.getMonth() + 1).padStart(2, '0')}`)
const cleanupTime = ref('02:30')
const cleanupWeekday = ref('1')
const cleanupDay = ref(1)

// MFA 专属模态弹窗状态
const mfaModalOpen = ref(false)
const isViewOnlyMfa = ref(false)
const qrDataUrl = ref('')
const mfaSecretText = ref('')
const modalVerifyCode = ref('')
const modalBusy = ref(false)

function bytes(value: unknown) {
  const size = Number(value || 0)
  if (size < 1024) return `${size} B`
  if (size < 1024 ** 2) return `${(size / 1024).toFixed(1)} KB`
  if (size < 1024 ** 3) return `${(size / 1024 ** 2).toFixed(1)} MB`
  return `${(size / 1024 ** 3).toFixed(2)} GB`
}

function asBoolean(value: unknown): boolean {
  return value === true || value === 1 || value === '1' || value === 'true'
}

function toggleCleanupScope(scope: string) {
  cleanupPreview.value = null
  cleanupScopes.value = cleanupScopes.value.includes(scope)
    ? cleanupScopes.value.filter((item) => item !== scope)
    : [...cleanupScopes.value, scope]
}

function selectCleanupPeriod(value: string) {
  cleanupPreview.value = null
  cleanupPeriod.value = value
  cleanupMenuOpen.value = false
  if (value !== 'month') {
    cleanupMonth.value = ''
    cleanupMonthMenuOpen.value = false
  }
}

function toggleCleanupMonthMenu() {
  if (!cleanupMonthMenuOpen.value) cleanupMonthYear.value = Number((cleanupMonth.value || latestCompletedMonth.value).slice(0, 4))
  cleanupMonthMenuOpen.value = !cleanupMonthMenuOpen.value
}

function selectCleanupMonth(month: number) {
  cleanupMonth.value = `${cleanupMonthYear.value}-${String(month).padStart(2, '0')}`
  cleanupMonthMenuOpen.value = false
}

function cleanupMonthLabel(): string {
  if (!cleanupMonth.value) return '选择月份'
  const [year, month] = cleanupMonth.value.split('-')
  return `${year} 年 ${Number(month)} 月`
}

watch(cleanupMonth, () => { cleanupPreview.value = null })

function cleanupFilters(): Row | null {
  if (!cleanupScopes.value.length || !cleanupPeriod.value) {
    notify('请选择清理内容和时间范围', 'error')
    return null
  }
  if (cleanupPeriod.value === 'month') {
    if (!cleanupMonth.value || cleanupMonth.value > latestCompletedMonth.value) {
      notify('只能清理已结束的月份', 'error')
      return null
    }
    return { scopes: [...cleanupScopes.value], month: cleanupMonth.value }
  }
  return { scopes: [...cleanupScopes.value], older_than_days: Number(cleanupPeriod.value) }
}

async function previewCleanup(page = 1) {
  if (cleanupPreviewBusy.value || busy.value) return
  const filters = cleanupFilters()
  if (!filters) return
  const selection = JSON.stringify([cleanupScopes.value, cleanupPeriod.value, cleanupMonth.value])
  cleanupPreviewBusy.value = true
  try {
    const response = await attempt(() => api<Row>('/storage/preview', 'POST', { ...filters, page, page_size: 20 }))
    if (response?.result && JSON.stringify([cleanupScopes.value, cleanupPeriod.value, cleanupMonth.value]) === selection) cleanupPreview.value = response.result
  } finally {
    cleanupPreviewBusy.value = false
  }
}

function previewKind(kind: unknown): string {
  return ({ screenshot: '截图', report: 'Word 报告', periodic_file: '周期报告',
    run_record: '巡检记录', periodic_record: '周期记录', log: '日志', file: '文件' } as Record<string, string>)[String(kind)] || '文件'
}

function closeCleanupMenu(event: PointerEvent) {
  if (!cleanupMenuRef.value?.contains(event.target as Node)) cleanupMenuOpen.value = false
  if (!cleanupMonthMenuRef.value?.contains(event.target as Node)) cleanupMonthMenuOpen.value = false
}

function cleanupPeriodLabel(): string {
  return cleanupPeriodOptions.find((item) => item.value === cleanupPeriod.value)?.label || '选择时间范围'
}

async function load(): Promise<boolean> {
  if (refreshing.value) return false
  refreshing.value = true
  try {
    const [stored, secured, docs] = await Promise.all([
      attempt(() => api<Row>('/storage')),
      attempt(() => api<Row>('/security-settings')),
      attempt(() => api<Row>('/swagger-settings')),
    ])
    if (stored?.config) {
      if (stored.latest_completed_month) latestCompletedMonth.value = String(stored.latest_completed_month)
      usage.value = stored.usage || {}
      Object.assign(storage, stored.config)
      storage.enabled = asBoolean(stored.config.enabled)
      storage.browser_state_cleanup_enabled = asBoolean(stored.config.browser_state_cleanup_enabled)
      try {
        const schedule = JSON.parse(String(stored.config.cleanup_schedule_config || '{}'))
        cleanupTime.value = String(schedule.time || '02:30')
        cleanupWeekday.value = String(schedule.day_of_week || '1')
        cleanupDay.value = Number(schedule.day_of_month || 1)
      } catch {
        cleanupTime.value = '02:30'
        cleanupWeekday.value = '1'
        cleanupDay.value = 1
      }
    }
    if (secured) Object.assign(security, secured)
    swagger.value = Boolean(docs?.enabled)
    return Boolean(stored?.config)
  } finally {
    refreshing.value = false
  }
}

async function saveStorage() {
  if (busy.value || refreshing.value) return
  pendingAction.value = 'storage'
  busy.value = true
  try {
    const requestedEnabled = asBoolean(storage.enabled)
    const schedule: Row = { time: cleanupTime.value }
    if (storage.cleanup_schedule_mode === 'weekly') schedule.day_of_week = cleanupWeekday.value
    if (storage.cleanup_schedule_mode === 'monthly') schedule.day_of_month = cleanupDay.value
    const result = await attempt(() => api<Row>('/storage/settings', 'PUT', {
      ...storage,
      cleanup_schedule_config: JSON.stringify(schedule),
      cleanup_schedule_label: '',
    }))
    if (result?.ok) {
      const loaded = await load()
      if (!loaded) notify('设置已提交，但无法读取保存结果，请刷新页面确认', 'error')
      else if (storage.enabled !== requestedEnabled) notify('自动清理状态未保存，请重试', 'error')
      else notify('存储设置已保存')
    }
  } finally {
    busy.value = false
    pendingAction.value = null
  }
}

async function saveSecurity() {
  if (busy.value) return
  pendingAction.value = 'security'
  busy.value = true
  const payload = {
    session_ttl_minutes: security.session_ttl_minutes,
  }
  const result = await attempt(() => api('/security-settings', 'PUT', payload), '安全设置已保存')
  busy.value = false
  pendingAction.value = null
  if (result) load()
}

async function cleanup() {
  if (busy.value) return
  const filters = cleanupFilters()
  if (!filters) return
  if (!cleanupPreview.value?.fingerprint || !Number(cleanupPreview.value.total_items)) {
    notify('请先查看待清理数据', 'error')
    return
  }
  const month = cleanupPeriod.value === 'month' ? cleanupMonth.value : undefined
  const scopes = [...cleanupScopes.value]
  const names = cleanupScopeOptions.filter((item) => scopes.includes(item.value)).map((item) => `${item.label}（${item.detail}）`).join('、')
  const period = month ? `${month} 的` : `${cleanupPeriodLabel()}的`
  busy.value = true
  try {
    const first = await confirmModal({
      title: '确认清理内容',
      message: `已预览 ${cleanupPreview.value.total_items} 项。清理内容：${names}。时间范围：${period}数据。${month ? '指定月份不受自动清理保留天数限制。' : ''}`,
      confirmText: '下一步',
      danger: true,
    })
    if (!first) return
    const second = await confirmModal({
      title: '再次确认删除',
      message: `将永久删除${period}${names}，无法恢复。确定继续吗？`,
      confirmText: '删除',
      danger: true,
    })
    if (!second) return
    pendingAction.value = 'cleanup'
    const result = await attempt(() => api<Row>('/storage/cleanup', 'POST', {
      ...filters, preview_fingerprint: cleanupPreview.value?.fingerprint,
    }))
    cleanupPreview.value = null
    if (result?.result) {
      const outcome = result.result
      if (outcome.status === 'partial_success') {
        notify(`部分清理完成，释放 ${bytes(outcome.deleted_bytes)}；${outcome.error_summary || '请查看清理记录'}`, 'error')
      } else {
        notify(`清理完成，删除 ${outcome.deleted_files_count || 0} 个文件，释放 ${bytes(outcome.deleted_bytes)}`)
      }
      await load()
    }
  } finally {
    busy.value = false
    pendingAction.value = null
  }
}

async function renderQr(url: string) {
  if (!url) {
    qrDataUrl.value = ''
    return
  }
  try {
    qrDataUrl.value = await QRCode.toDataURL(url, {
      width: 220,
      margin: 1,
      color: {
        dark: '#1e3040',
        light: '#ffffff',
      },
    })
  } catch (err) {
    console.error('QR code generation failed', err)
    qrDataUrl.value = ''
  }
}

// 生成 / 刷新密钥并渲染二维码
async function refreshMfaSecret(showNotice = false) {
  modalBusy.value = true
  qrDataUrl.value = ''
  const result = await attempt(() => api<Row>('/security-settings/mfa-secret', 'POST', {}))
  modalBusy.value = false
  if (result) {
    mfaSecretText.value = String(result.mfa_secret || result.secret || '')
    await renderQr(String(result.mfa_otpauth_url || result.qr_uri || ''))
    if (showNotice) notify('已刷新两步验证密钥与二维码')
  }
}

// 打开绑定弹窗
async function openBindModal() {
  isViewOnlyMfa.value = false
  modalVerifyCode.value = ''
  mfaModalOpen.value = true
  await refreshMfaSecret(false)
}

// 打开查看当前已有绑定二维码弹窗
async function openViewQrModal() {
  isViewOnlyMfa.value = true
  modalVerifyCode.value = ''
  qrDataUrl.value = ''
  mfaSecretText.value = ''
  mfaModalOpen.value = true
  modalBusy.value = true
  const result = await attempt(() => api<Row>('/security-settings/mfa-qr', 'GET'))
  modalBusy.value = false
  if (result) {
    mfaSecretText.value = String(result.mfa_secret || '')
    await renderQr(String(result.mfa_otpauth_url || result.qr_uri || ''))
  }
}

// 关闭弹窗（未确认时绝不修改任何启用状态）
function closeMfaModal() {
  mfaModalOpen.value = false
  modalVerifyCode.value = ''
  qrDataUrl.value = ''
  mfaSecretText.value = ''
}

// 在弹窗中提交 6 位验证码以完成绑定并开启
async function submitMfaBind() {
  const code = modalVerifyCode.value.trim()
  if (code.length !== 6) {
    notify('请输入完整的 6 位动态验证码', 'error')
    return
  }
  modalBusy.value = true
  const result = await attempt(
    () => api('/security-settings', 'PUT', { mfa_enabled: true, mfa_code: code }),
    '两步验证（MFA）已成功开启并绑定！'
  )
  modalBusy.value = false
  if (result) {
    security.mfa_enabled = true
    security.mfa_configured = true
    closeMfaModal()
    load()
  }
}

// 点击开关处理
async function onMfaSwitchToggle(e: Event) {
  const target = e.target as HTMLInputElement
  if (security.mfa_enabled) {
    // 当前已开启，用户想要关闭 -> 弹出危险确认
    target.checked = true
    const ok = await confirmModal({
      title: '关闭两步验证（MFA）',
      message: '关闭两步验证后，登录将仅依赖密码，账号安全性将降低。确定要关闭两步验证吗？',
      confirmText: '关闭',
      danger: true,
    })
    if (!ok) return
    busy.value = true
    const result = await attempt(() => api('/security-settings', 'PUT', { mfa_enabled: false }), '两步验证（MFA）已关闭')
    busy.value = false
    if (result) {
      security.mfa_enabled = false
      load()
    }
  } else {
    // 当前未开启，用户点击想要开启 -> 恢复未勾选，弹出专属绑定扫码弹窗
    target.checked = false
    await openBindModal()
  }
}

async function copySecretText() {
  if (!mfaSecretText.value) return
  try {
    await navigator.clipboard.writeText(mfaSecretText.value)
    notify('密钥已复制到剪贴板')
  } catch {
    notify('复制失败，请手动选中文本复制')
  }
}

async function updateDocs() {
  const result = await attempt(() => api('/swagger-settings', 'PUT', { enabled: swagger.value }), '接口文档设置已保存')
  if (!result) swagger.value = !swagger.value
}

function scrollToSection(id: string) {
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

onMounted(async () => {
  document.addEventListener('pointerdown', closeCleanupMenu)
  await load()
  const section = route.query.section
  if (section === 'storage' || section === 'security') {
    scrollToSection(`${section}-section`)
  }
})
onUnmounted(() => document.removeEventListener('pointerdown', closeCleanupMenu))
</script>

<template>
  <section class="page-intro"><div><h1>系统设置</h1></div><ButtonGroup class="page-actions"><RefreshButton :loading="refreshing || busy" @click="load" /></ButtonGroup></section>
  <nav class="settings-tabs" aria-label="设置分类"><button type="button" aria-controls="storage-section" @click="scrollToSection('storage-section')">数据存储</button><button type="button" aria-controls="security-section" @click="scrollToSection('security-section')">安全设置</button></nav>
  <section id="storage-section" class="settings-section"><div class="section-heading"><div><h2>数据存储</h2></div></div><div class="storage-summary"><div><HardDrive :size="21" /><span>总占用</span><strong>{{ bytes(usage.total_bytes) }}</strong></div><div><Database :size="21" /><span>截图</span><strong>{{ bytes(usage.screenshots_bytes) }}</strong></div><div><Database :size="21" /><span>报告</span><strong>{{ bytes(usage.reports_bytes) }}</strong></div><div><Database :size="21" /><span>日志</span><strong>{{ bytes(usage.logs_bytes) }}</strong></div></div>
    <form class="form-section settings-form auto-cleanup-form" @submit.prevent="saveStorage">
      <div class="form-section-head"><div><h2>自动清理</h2></div></div>
      <label class="switch-row auto-cleanup-switch"><input v-model="storage.enabled" type="checkbox" /><strong>启用自动清理</strong></label>
      <fieldset class="form-grid auto-cleanup-fields" :disabled="!storage.enabled">
        <label class="field"><span>清理频率</span><select v-model="storage.cleanup_schedule_mode"><option value="daily">每天</option><option value="weekly">每周</option><option value="monthly">每月</option></select></label>
        <label class="field"><span>执行时间</span><input v-model="cleanupTime" type="time" required /></label>
        <label v-if="storage.cleanup_schedule_mode === 'weekly'" class="field"><span>每周几</span><select v-model="cleanupWeekday"><option value="1">周一</option><option value="2">周二</option><option value="3">周三</option><option value="4">周四</option><option value="5">周五</option><option value="6">周六</option><option value="7">周日</option></select></label>
        <label v-if="storage.cleanup_schedule_mode === 'monthly'" class="field"><span>每月几号</span><input v-model.number="cleanupDay" type="number" min="1" max="31" required /></label>
        <label class="field"><span>截图保留（天）</span><input v-model.number="storage.screenshot_retention_days" type="number" min="1" /></label>
        <label class="field"><span>报告保留（天）</span><input v-model.number="storage.report_retention_days" type="number" min="1" /></label>
        <label class="field"><span>运行记录保留（天）</span><input v-model.number="storage.run_record_retention_days" type="number" min="1" /></label>
        <label class="field"><span>日志保留（天）</span><input v-model.number="storage.log_retention_days" type="number" min="1" /></label>
        <label class="field"><span>最近数据保护（天）</span><input v-model.number="storage.protect_recent_days" type="number" min="1" /></label>
      </fieldset>
      <ButtonGroup class="editor-footer"><ActionButton type="submit" :disabled="busy" variant="primary" :loading="pendingAction === 'storage'">保存配置</ActionButton></ButtonGroup>
    </form>
    <div class="form-section settings-form manual-cleanup-section">
      <div class="form-section-head"><div><h2>手动清理</h2><p>选择内容和时间，确认后删除。</p></div></div>
      <div class="manual-cleanup-layout">
        <div class="manual-cleanup-group">
          <h3>清理内容</h3>
          <div class="manual-cleanup-scopes">
            <button v-for="item in cleanupScopeOptions" :key="item.value" type="button" class="manual-cleanup-scope" :class="{ selected: cleanupScopes.includes(item.value) }" :aria-pressed="cleanupScopes.includes(item.value)" @click="toggleCleanupScope(item.value)">
              <span class="manual-scope-check" aria-hidden="true"></span>
              <span class="manual-scope-copy"><strong>{{ item.label }}</strong><small>{{ item.detail }}</small></span>
            </button>
          </div>
        </div>
        <div class="manual-cleanup-group manual-cleanup-period-group">
          <h3>时间范围</h3>
          <div ref="cleanupMenuRef" class="manual-cleanup-select" :class="{ open: cleanupMenuOpen }" @keydown.esc="cleanupMenuOpen = false">
            <button type="button" class="manual-cleanup-select-trigger" aria-haspopup="listbox" :aria-expanded="cleanupMenuOpen" @click="cleanupMenuOpen = !cleanupMenuOpen">
              <span :class="{ placeholder: !cleanupPeriod }">{{ cleanupPeriodLabel() }}</span><span class="manual-select-chevron" aria-hidden="true"></span>
            </button>
            <Transition name="cleanup-menu">
              <div v-if="cleanupMenuOpen" class="manual-cleanup-select-menu" role="listbox" aria-label="清理时间范围">
                <button v-for="item in cleanupPeriodOptions" :key="item.value" type="button" class="manual-cleanup-select-option" role="option" :aria-selected="cleanupPeriod === item.value" @click="selectCleanupPeriod(item.value)">{{ item.label }}</button>
              </div>
            </Transition>
          </div>
          <div v-if="cleanupPeriod === 'month'" class="manual-cleanup-month">
            <span class="manual-cleanup-month-label">选择月份</span>
            <div ref="cleanupMonthMenuRef" class="manual-cleanup-month-picker" :class="{ open: cleanupMonthMenuOpen }" @keydown.esc="cleanupMonthMenuOpen = false">
              <button type="button" class="manual-cleanup-select-trigger manual-cleanup-month-trigger" aria-haspopup="dialog" :aria-expanded="cleanupMonthMenuOpen" @click="toggleCleanupMonthMenu">
                <span :class="{ placeholder: !cleanupMonth }">{{ cleanupMonthLabel() }}</span><span class="manual-month-calendar-icon" aria-hidden="true"></span>
              </button>
              <Transition name="cleanup-menu">
                <div v-if="cleanupMonthMenuOpen" class="manual-cleanup-month-menu" role="dialog" aria-label="选择清理月份">
                  <div class="manual-cleanup-month-heading">
                    <button type="button" class="manual-cleanup-month-nav" aria-label="上一年" :disabled="cleanupMonthYear <= 1" @click="cleanupMonthYear--"><ChevronLeft :size="16" /></button>
                    <strong>{{ cleanupMonthYear }} 年</strong>
                    <button type="button" class="manual-cleanup-month-nav" aria-label="下一年" :disabled="cleanupMonthYear >= Number(latestCompletedMonth.slice(0, 4))" @click="cleanupMonthYear++"><ChevronRight :size="16" /></button>
                  </div>
                  <div class="manual-cleanup-month-grid">
                    <button v-for="month in 12" :key="month" type="button" class="manual-cleanup-month-option" :class="{ selected: cleanupMonth === `${cleanupMonthYear}-${String(month).padStart(2, '0')}` }" :disabled="`${cleanupMonthYear}-${String(month).padStart(2, '0')}` > latestCompletedMonth" :aria-label="`${cleanupMonthYear} 年 ${month} 月`" :aria-pressed="cleanupMonth === `${cleanupMonthYear}-${String(month).padStart(2, '0')}`" @click="selectCleanupMonth(month)">{{ month }} 月</button>
                  </div>
                </div>
              </Transition>
            </div>
          </div>
        </div>
      </div>
      <div class="manual-cleanup-preview-action"><ActionButton :disabled="busy || cleanupPreviewBusy || !cleanupScopes.length || !cleanupPeriod || (cleanupPeriod === 'month' && (!cleanupMonth || cleanupMonth > latestCompletedMonth))" :loading="cleanupPreviewBusy" @click="previewCleanup(1)">{{ cleanupPreview ? '刷新预览' : '预览数据' }}</ActionButton></div>
      <div v-if="cleanupPreview" class="manual-cleanup-preview" aria-live="polite">
        <div class="manual-cleanup-preview-head"><strong>待清理数据</strong><span>共 {{ cleanupPreview.total_items }} 项</span></div>
        <p v-if="!Number(cleanupPreview.total_items)" class="manual-cleanup-preview-empty">当前选择没有可清理的数据</p>
        <div v-else class="manual-cleanup-preview-list">
          <div v-for="item in (cleanupPreview.items || [])" :key="`${item.kind}:${item.path}`" class="manual-cleanup-preview-row">
            <span class="manual-cleanup-preview-kind">{{ previewKind(item.kind) }}</span>
            <div class="manual-cleanup-preview-name"><TableText class="table-name-strong" :text="item.name" /><TableText v-if="!String(item.kind).endsWith('_record')" class="table-path" :text="item.path" /></div>
            <span class="manual-cleanup-preview-date">{{ item.date || '时间未知' }}</span>
            <span class="manual-cleanup-preview-size">{{ item.size == null ? '记录' : bytes(item.size) }}</span>
          </div>
        </div>
        <div v-if="Number(cleanupPreview.total_items) > Number(cleanupPreview.page_size)" class="manual-cleanup-preview-pages"><span>第 {{ cleanupPreview.page }} / {{ Math.ceil(Number(cleanupPreview.total_items) / Number(cleanupPreview.page_size)) }} 页</span><ActionButton class="btn-small" :disabled="cleanupPreviewBusy || Number(cleanupPreview.page) <= 1" @click="previewCleanup(Number(cleanupPreview.page) - 1)">上一页</ActionButton><ActionButton class="btn-small" :disabled="cleanupPreviewBusy || Number(cleanupPreview.page) * Number(cleanupPreview.page_size) >= Number(cleanupPreview.total_items)" @click="previewCleanup(Number(cleanupPreview.page) + 1)">下一页</ActionButton></div>
      </div>
      <div class="manual-cleanup-footer"><span>清理后无法恢复</span><ActionButton type="button" :disabled="busy || cleanupPreviewBusy || !Number(cleanupPreview?.total_items)" @click="cleanup" variant="danger" :loading="pendingAction === 'cleanup'">立即清理</ActionButton></div>
    </div>
  </section>
  <section id="security-section" class="settings-section">
    <div class="section-heading"><div><h2>安全设置</h2></div></div>
    <form class="form-section settings-form" @submit.prevent="saveSecurity">
      <div class="form-section-head"><div><h2>账户安全</h2></div></div>
      <div class="form-grid">
        <label class="field">
          <span>登录有效期（分钟）</span>
          <input v-model.number="security.session_ttl_minutes" type="number" min="5" max="10080" />
        </label>

        <!-- 两步验证独立交互行 -->
        <div class="mfa-switch-row field-full">
          <label class="switch-row">
            <input
              type="checkbox"
              :checked="Boolean(security.mfa_enabled)"
              @change="onMfaSwitchToggle"
            />
            <strong>启用两步验证（MFA）</strong>
          </label>
          <div v-if="security.mfa_enabled" class="mfa-switch-actions">
            <ActionButton class="btn-small" type="button" @click="openViewQrModal" variant="secondary">
              二维码
            </ActionButton>
            <ActionButton class="btn-small" type="button" @click="openBindModal" variant="secondary">
              重新绑定
            </ActionButton>
          </div>
        </div>

        <label class="switch-row field-full">
          <input v-model="swagger" type="checkbox" @change="updateDocs" />启用 API 接口文档
        </label>
      </div>

      <ButtonGroup class="editor-footer">
        <ActionButton type="submit" :disabled="busy" variant="primary" :loading="pendingAction === 'security'">保存配置</ActionButton>
      </ButtonGroup>
    </form>
  </section>

  <!-- MFA 绑定与二维码模态弹窗 -->
  <Transition name="modal-fade">
    <div v-if="mfaModalOpen" class="modal-backdrop" @click="closeMfaModal">
      <div class="mfa-dialog-box" role="dialog" aria-modal="true" @click.stop>
        <div class="mfa-dialog-header">
          <div class="mfa-dialog-title">
            <ShieldCheck :size="20" class="mfa-title-icon" />
            <h3>{{ isViewOnlyMfa ? '查看两步验证二维码' : '绑定两步验证（MFA）' }}</h3>
          </div>
          <IconButton class="icon-button" type="button" aria-label="关闭" @click="closeMfaModal" label="关闭">
            <X :size="18" />
          </IconButton>
        </div>

        <div class="mfa-dialog-body">
          <p class="mfa-dialog-intro">
            请使用 <strong>Google Authenticator</strong>、<strong>Microsoft Authenticator</strong> 或微信<strong>“腾讯身份验证器”</strong>小程序扫描下方二维码。
          </p>

          <!-- 二维码展示区（支持点击刷新） -->
          <div class="mfa-qr-center-box">
            <div
              class="mfa-qr-interactive"
              :class="{ 'clickable': !isViewOnlyMfa }"
              :title="!isViewOnlyMfa ? '若二维码有问题，点击可刷新重新生成' : ''"
              @click="!isViewOnlyMfa && refreshMfaSecret(true)"
            >
              <img v-if="qrDataUrl" :src="qrDataUrl" alt="两步验证二维码" class="mfa-qr-canvas-img" />
              <div v-else class="mfa-qr-loading-box">
                <RefreshCw :size="24" class="spin" />
                <span>加载二维码中...</span>
              </div>
              <div v-if="!isViewOnlyMfa && qrDataUrl" class="mfa-qr-refresh-overlay">
                <RefreshCw :size="20" />
                <span>点击刷新二维码</span>
              </div>
            </div>
            <div v-if="!isViewOnlyMfa" class="mfa-qr-subtip" @click="refreshMfaSecret(true)">
              <RefreshCw :size="12" /> 二维码无法扫描？点击刷新重新生成
            </div>
          </div>

          <!-- 手动添加密钥 -->
          <div class="mfa-dialog-manual-row">
            <span>若无法扫码，可手动添加密钥：</span>
            <div class="mfa-secret-code-pill">
              <code>{{ mfaSecretText }}</code>
              <ActionButton class="btn-small" type="button" @click="copySecretText" variant="secondary">
                复制密钥
              </ActionButton>
            </div>
          </div>

          <!-- 动态验证码输入（仅在绑定模式下显示） -->
          <div v-if="!isViewOnlyMfa" class="mfa-dialog-verify-section">
            <label class="field">
              <span>动态验证码 <b>*</b></span>
              <input
                v-model="modalVerifyCode"
                class="mfa-dialog-code-input"
                inputmode="numeric"
                maxlength="6"
                placeholder=""
                autocomplete="one-time-code"
                @keyup.enter="submitMfaBind"
              />
            </label>
          </div>
        </div>

        <ButtonGroup class="mfa-dialog-footer">
          <ActionButton type="button" @click="closeMfaModal" variant="secondary">
            {{ isViewOnlyMfa ? '关闭' : '取消' }}
          </ActionButton>
          <ActionButton
            v-if="!isViewOnlyMfa"

            type="button"
            :disabled="modalBusy || modalVerifyCode.trim().length !== 6"
            @click="submitMfaBind"
           variant="primary">

            确认绑定
          </ActionButton>
        </ButtonGroup>
      </div>
    </div>
  </Transition>
</template>
