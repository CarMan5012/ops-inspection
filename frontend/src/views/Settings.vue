<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import QRCode from 'qrcode'
import { Database, ShieldCheck, RefreshCw, HardDrive, X, ChevronLeft, ChevronRight } from '../icons'
import { api, type Row } from '../api'
import { attempt, notify, confirmModal } from '../ui'
import RefreshButton from '../components/RefreshButton.vue'
import PasswordInput from '../components/PasswordInput.vue'

const usage = ref<Row>({})
const disk = ref<Row | null>(null)
const diskAlert = reactive<Row>({ enabled: false, threshold: 80, profile_id: 0 })
const dingtalkProfiles = ref<Row[]>([])
const diskAlertSaving = ref(false)
const route = useRoute()
const router = useRouter()
type SettingsSection = 'general' | 'storage' | 'security'
const activeSection = ref<SettingsSection>('general')
const storage = reactive<Row>({})
const security = reactive<Row>({})
const systemPrefs = reactive<Row>({})
const systemSaving = ref(false)
const adminPassword = reactive({ current: '', next: '', confirm: '' })
const passwordBusy = ref(false)
const adminUsername = reactive({ next: '', currentPassword: '' })
const usernameBusy = ref(false)
const accountModal = ref<'username' | 'password' | null>(null)
const accountError = ref('')
const swagger = ref(false)
const busy = ref(false)
const pendingAction = ref<'storage' | 'cleanup' | null>(null)
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
const cleanupScopeOptions = ref<Row[]>([])
const cleanupPeriodOptions = ref<Row[]>([])
const cleanupScheduleModes = ref<Row[]>([])
const configLoading = ref(true)
const configError = ref(false)
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
const modalCurrentCode = ref('')
const modalBusy = ref(false)
const mfaDisableOpen = ref(false)
const mfaDisableCode = ref('')

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

async function saveAdminUsername() {
  if (usernameBusy.value) return
  const username = adminUsername.next.trim()
  if (!/^[A-Za-z0-9_.-]{3,32}$/.test(username) || username === systemPrefs.admin_username) {
    accountError.value = '请输入不同于当前账号的 3–32 位用户名'
    return
  }
  if (!adminUsername.currentPassword) {
    accountError.value = '请输入当前密码'
    return
  }
  accountError.value = ''
  usernameBusy.value = true
  try {
    const result = await attempt(() => api<Row>('/admin/username', 'PUT', {
      username, current_password: adminUsername.currentPassword,
    }))
    if (result?.ok) window.location.assign(`${String(systemPrefs.access_context || '')}/login`)
  } finally {
    usernameBusy.value = false
  }
}

async function saveAdminPassword() {
  if (passwordBusy.value) return
  if (!adminPassword.current) {
    accountError.value = '请输入当前密码'
    return
  }
  if (adminPassword.next.length < 12 || adminPassword.next.length > 128) {
    accountError.value = '新密码需为 12–128 位'
    return
  }
  if (adminPassword.next !== adminPassword.confirm) {
    accountError.value = '两次输入的新密码不一致'
    return
  }
  accountError.value = ''
  passwordBusy.value = true
  try {
    const result = await attempt(() => api<Row>('/admin/password', 'PUT', {
      current_password: adminPassword.current,
      new_password: adminPassword.next,
    }))
    if (result?.ok) {
      adminPassword.current = ''
      adminPassword.next = ''
      adminPassword.confirm = ''
      window.location.assign(`${window.location.pathname.replace(/\/$/, '')}/login`)
    }
  } finally {
    passwordBusy.value = false
  }
}

async function openAccountModal(kind: 'username' | 'password') {
  accountError.value = ''
  adminUsername.next = ''
  adminUsername.currentPassword = ''
  adminPassword.current = ''
  adminPassword.next = ''
  adminPassword.confirm = ''
  accountModal.value = kind
  await nextTick()
  document.querySelector<HTMLInputElement>('.account-dialog-box input')?.focus()
}

function closeAccountModal() {
  if (usernameBusy.value || passwordBusy.value) return
  accountModal.value = null
  accountError.value = ''
  adminUsername.currentPassword = ''
  adminPassword.current = ''
  adminPassword.next = ''
  adminPassword.confirm = ''
}

async function saveSystemPrefs() {
  if (systemSaving.value) return
  const name = String(systemPrefs.app_name || '').trim()
  const primary = String(systemPrefs.holiday_primary_url || '').trim()
  const backup = String(systemPrefs.holiday_backup_url || '').trim()
  if (!name || name.length > 40) {
    notify('系统名称需为 1–40 个字符', 'error')
    return
  }
  if (!primary.includes('{year}') || !backup.includes('{year}')) {
    notify('节假日接口地址需要包含 {year}', 'error')
    return
  }
  const context = String(systemPrefs.access_context || '').trim()
  if (context && context !== '/' && !/^\/[A-Za-z0-9_-]+(?:\/[A-Za-z0-9_-]+)*$/.test(context)) {
    notify('访问路径前缀需留空或填写 /ops 这样的路径', 'error')
    return
  }
  systemSaving.value = true
  try {
    const result = await attempt(() => api<Row>('/system-preferences', 'PUT', {
      app_name: name, access_context: context, holiday_primary_url: primary, holiday_backup_url: backup,
    }))
    if (result?.ok) {
      notify('基础设置已保存')
      window.setTimeout(() => window.location.assign(`${String(result.access_context || '')}/#/settings?section=general`), 500)
    }
  } finally {
    systemSaving.value = false
  }
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
  return cleanupPeriodOptions.value.find((item) => item.value === cleanupPeriod.value)?.label || '选择时间范围'
}

async function loadUiConfig() {
  configLoading.value = true
  const config = await attempt(() => api<Row>('/ui-config'))
  configError.value = !Array.isArray(config?.cleanup_scopes) || !Array.isArray(config?.cleanup_periods) || !Array.isArray(config?.cleanup_schedule_modes)
  if (!configError.value) {
    cleanupScopeOptions.value = config!.cleanup_scopes
    cleanupPeriodOptions.value = config!.cleanup_periods
    cleanupScheduleModes.value = config!.cleanup_schedule_modes
  }
  configLoading.value = false
}

async function load(): Promise<boolean> {
  if (refreshing.value) return false
  refreshing.value = true
  try {
    const [stored, secured, docs, preferences, profiles] = await Promise.all([
      attempt(() => api<Row>('/storage')),
      attempt(() => api<Row>('/security-settings')),
      attempt(() => api<Row>('/swagger-settings')),
      attempt(() => api<Row>('/system-preferences')),
      attempt(() => api<Row>('/dingtalk-profiles')),
    ])
    if (stored?.config) {
      if (stored.latest_completed_month) latestCompletedMonth.value = String(stored.latest_completed_month)
      usage.value = stored.usage || {}
      disk.value = stored.disk || null
      if (stored.disk_alert) Object.assign(diskAlert, stored.disk_alert)
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
    if (preferences) Object.assign(systemPrefs, preferences)
    dingtalkProfiles.value = profiles?.profiles || []
    swagger.value = Boolean(docs?.enabled)
    return Boolean(stored?.config)
  } finally {
    refreshing.value = false
  }
}

async function saveDiskAlert() {
  if (diskAlertSaving.value) return
  const threshold = Number(diskAlert.threshold)
  if (!Number.isInteger(threshold) || threshold < 1 || threshold > 99) {
    notify('告警阈值需为 1–99%', 'error')
    return
  }
  if (diskAlert.enabled && !Number(diskAlert.profile_id)) {
    notify('请选择钉钉机器人', 'error')
    return
  }
  diskAlertSaving.value = true
  try {
    const result = await attempt(() => api<Row>('/storage/disk-alert', 'PUT', {
      enabled: Boolean(diskAlert.enabled), threshold, profile_id: Number(diskAlert.profile_id) || 0,
    }))
    if (result?.ok) {
      Object.assign(diskAlert, result.disk_alert)
      notify('磁盘告警设置已保存')
    }
  } finally {
    diskAlertSaving.value = false
  }
}

async function saveStorage() {
  if (busy.value || refreshing.value || configLoading.value || configError.value) return
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
  const names = cleanupScopeOptions.value.filter((item) => scopes.includes(item.value)).map((item) => `${item.label}（${item.detail}）`).join('、')
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
  if (modalBusy.value) return
  modalBusy.value = true
  qrDataUrl.value = ''
  mfaSecretText.value = ''
  modalVerifyCode.value = ''
  const result = await attempt(() => api<Row>('/security-settings/mfa-secret', 'POST', {}))
  if (result) {
    mfaSecretText.value = String(result.mfa_secret || result.secret || '')
    await renderQr(String(result.mfa_otpauth_url || result.qr_uri || ''))
    if (showNotice) notify('已刷新两步验证密钥与二维码')
  }
  modalBusy.value = false
}

// 打开绑定弹窗
async function openBindModal() {
  isViewOnlyMfa.value = false
  modalVerifyCode.value = ''
  modalCurrentCode.value = ''
  mfaModalOpen.value = true
  await refreshMfaSecret(false)
}

// 打开查看当前已有绑定二维码弹窗
async function openViewQrModal() {
  isViewOnlyMfa.value = true
  modalVerifyCode.value = ''
  modalCurrentCode.value = ''
  qrDataUrl.value = ''
  mfaSecretText.value = ''
  mfaModalOpen.value = true
}

async function revealCurrentMfaQr() {
  if (modalBusy.value) return
  const code = modalCurrentCode.value.trim()
  if (!/^\d{6}$/.test(code)) {
    notify('请输入当前设备的 6 位动态验证码', 'error')
    return
  }
  modalBusy.value = true
  const result = await attempt(() => api<Row>('/security-settings/mfa-qr', 'POST', { mfa_current_code: code }))
  if (result) {
    mfaSecretText.value = String(result.mfa_secret || '')
    await renderQr(String(result.mfa_otpauth_url || result.qr_uri || ''))
  }
  modalBusy.value = false
}

// 关闭弹窗（未确认时绝不修改任何启用状态）
function closeMfaModal() {
  mfaModalOpen.value = false
  modalVerifyCode.value = ''
  modalCurrentCode.value = ''
  qrDataUrl.value = ''
  mfaSecretText.value = ''
}

// 在弹窗中提交 6 位验证码以完成绑定并开启
async function submitMfaBind() {
  if (modalBusy.value) return
  const code = modalVerifyCode.value.trim()
  if (!/^\d{6}$/.test(code)) {
    notify('请输入完整的 6 位动态验证码', 'error')
    return
  }
  if (security.mfa_enabled && !/^\d{6}$/.test(modalCurrentCode.value.trim())) {
    notify('请输入当前设备的 6 位动态验证码', 'error')
    return
  }
  modalBusy.value = true
  const result = await attempt(
    () => api('/security-settings', 'PUT', { mfa_enabled: true, mfa_code: code, mfa_current_code: modalCurrentCode.value.trim() }),
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

async function submitMfaDisable() {
  if (busy.value) return
  const code = mfaDisableCode.value.trim()
  if (!/^\d{6}$/.test(code)) {
    notify('请输入当前设备的 6 位动态验证码', 'error')
    return
  }
  busy.value = true
  try {
    const result = await attempt(() => api('/security-settings', 'PUT', { mfa_enabled: false, mfa_current_code: code }), '两步验证（MFA）已关闭')
    if (result) {
      security.mfa_enabled = false
      mfaDisableOpen.value = false
      mfaDisableCode.value = ''
      await load()
    }
  } finally {
    busy.value = false
  }
}

// 点击开关处理
async function onMfaSwitchToggle(e: Event) {
  const target = e.target as HTMLInputElement
  if (security.mfa_enabled) {
    target.checked = true
    mfaDisableCode.value = ''
    mfaDisableOpen.value = true
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

function isSettingsSection(value: unknown): value is SettingsSection {
  return value === 'general' || value === 'storage' || value === 'security'
}

function selectSection(section: SettingsSection) {
  activeSection.value = section
  void router.replace({ query: { ...route.query, section } })
}

watch(() => route.query.section, (section) => {
  activeSection.value = isSettingsSection(section) ? section : 'general'
}, { immediate: true })

onMounted(async () => {
  document.addEventListener('pointerdown', closeCleanupMenu)
  await Promise.all([loadUiConfig(), load()])
})
onUnmounted(() => document.removeEventListener('pointerdown', closeCleanupMenu))
</script>

<template>
  <div class="settings-page">
  <section class="page-intro settings-intro"><div><h1>系统设置</h1><p>管理系统信息、数据保留与账户安全</p></div><ButtonGroup class="page-actions"><RefreshButton :loading="refreshing || busy" @click="load" /></ButtonGroup></section>
  <nav class="settings-tabs settings-category-tabs" role="tablist" aria-label="设置分类"><button type="button" role="tab" id="general-tab" aria-controls="general-section" :aria-selected="activeSection === 'general'" :class="{ active: activeSection === 'general' }" @click="selectSection('general')">基础设置</button><button type="button" role="tab" id="storage-tab" aria-controls="storage-section" :aria-selected="activeSection === 'storage'" :class="{ active: activeSection === 'storage' }" @click="selectSection('storage')">数据存储</button><button type="button" role="tab" id="security-tab" aria-controls="security-section" :aria-selected="activeSection === 'security'" :class="{ active: activeSection === 'security' }" @click="selectSection('security')">安全设置</button></nav>
  <section v-show="activeSection === 'general'" id="general-section" class="settings-section" role="tabpanel" aria-labelledby="general-tab">
    <div class="section-heading"><div><h2>基础设置</h2></div></div>
    <form class="form-section settings-form system-preferences-form" novalidate @submit.prevent="saveSystemPrefs">
      <div class="form-grid">
        <label class="field"><span>系统名称</span><input v-model="systemPrefs.app_name" maxlength="40" required placeholder="例如 运维巡检平台"></label>
        <label class="field"><span>访问路径前缀</span><input v-model="systemPrefs.access_context" placeholder="例如 /ops；留空为根路径" maxlength="80"></label>
      </div>
      <div class="system-preferences-holiday">
        <h3>节假日数据源</h3>
        <div class="form-grid">
          <label class="field"><span>主接口地址</span><input v-model="systemPrefs.holiday_primary_url" type="text" spellcheck="false" placeholder="包含 {year} 的 HTTP(S) 地址"></label>
          <label class="field"><span>备用接口地址</span><input v-model="systemPrefs.holiday_backup_url" type="text" spellcheck="false" placeholder="包含 {year} 的 HTTP(S) 地址"></label>
        </div>
      </div>
      <div class="system-preferences-actions"><ActionButton type="submit" variant="primary" :disabled="systemSaving || !systemPrefs.app_name">保存基础设置</ActionButton></div>
    </form>
  </section>
  <section v-show="activeSection === 'storage'" id="storage-section" class="settings-section" role="tabpanel" aria-labelledby="storage-tab"><div class="section-heading"><div><h2>数据存储</h2></div></div>
    <div v-if="disk" class="form-section settings-form disk-status-card">
      <div class="disk-status-heading"><div><HardDrive :size="21" /><strong>磁盘空间</strong><span class="disk-status-badge" :class="{ warning: Number(disk.used_percent) >= Number(diskAlert.threshold) }">{{ Number(disk.used_percent) >= Number(diskAlert.threshold) ? '达到阈值' : '正常' }}</span></div><small>数据目录：{{ disk.path }}</small></div>
      <div class="disk-status-line"><strong>{{ disk.used_percent }}%</strong><span>已用 {{ bytes(disk.used_bytes) }} / 总容量 {{ bytes(disk.total_bytes) }} · 可用 {{ bytes(disk.free_bytes) }}</span></div>
      <div class="disk-status-track" role="progressbar" :aria-valuenow="Number(disk.used_percent)" aria-valuemin="0" aria-valuemax="100" aria-label="磁盘已用比例"><span :style="{ width: `${Math.min(100, Number(disk.used_percent))}%` }"></span><i :style="{ left: `${Number(diskAlert.threshold)}%` }"></i></div>
      <div class="disk-status-scale"><span>0%</span><span>告警阈值 {{ diskAlert.threshold }}%</span><span>100%</span></div>
      <form class="disk-alert-form" @submit.prevent="saveDiskAlert">
        <label class="switch-row"><input v-model="diskAlert.enabled" type="checkbox" /><strong>启用钉钉磁盘告警</strong></label>
        <label class="field"><span>告警阈值（%）</span><input v-model.number="diskAlert.threshold" type="number" min="1" max="99" /></label>
        <label class="field"><span>钉钉机器人</span><select v-model.number="diskAlert.profile_id"><option :value="0">请选择</option><option v-for="profile in dingtalkProfiles" :key="profile.id" :value="Number(profile.id)">{{ profile.name }}</option></select></label>
        <ActionButton type="submit" variant="primary" :disabled="diskAlertSaving" :loading="diskAlertSaving">保存告警设置</ActionButton>
      </form>
    </div>
    <div class="storage-summary"><div><HardDrive :size="21" /><span>应用数据</span><strong>{{ bytes(usage.total_bytes) }}</strong></div><div><Database :size="21" /><span>截图</span><strong>{{ bytes(usage.screenshots_bytes) }}</strong></div><div><Database :size="21" /><span>报告</span><strong>{{ bytes(usage.reports_bytes) }}</strong></div><div><Database :size="21" /><span>日志</span><strong>{{ bytes(usage.logs_bytes) }}</strong></div></div>
    <div class="storage-panels">
    <form class="form-section settings-form auto-cleanup-form" @submit.prevent="saveStorage">
      <div class="form-section-head"><div><h2>自动清理</h2></div></div>
      <label class="switch-row auto-cleanup-switch"><input v-model="storage.enabled" type="checkbox" /><strong>启用自动清理</strong></label>
      <fieldset class="form-grid auto-cleanup-fields" :disabled="!storage.enabled || configLoading || configError">
        <label class="field"><span>清理频率</span><select v-model="storage.cleanup_schedule_mode"><option v-for="option in cleanupScheduleModes" :key="option.value" :value="option.value">{{ option.label }}</option></select></label>
        <label class="field"><span>执行时间</span><input v-model="cleanupTime" type="time" required /></label>
        <label v-if="storage.cleanup_schedule_mode === 'weekly'" class="field"><span>每周几</span><select v-model="cleanupWeekday"><option value="1">周一</option><option value="2">周二</option><option value="3">周三</option><option value="4">周四</option><option value="5">周五</option><option value="6">周六</option><option value="7">周日</option></select></label>
        <label v-if="storage.cleanup_schedule_mode === 'monthly'" class="field"><span>每月几号</span><input v-model.number="cleanupDay" type="number" min="1" max="31" required /></label>
        <label class="field"><span>截图保留（天）</span><input v-model.number="storage.screenshot_retention_days" type="number" min="1" /></label>
        <label class="field"><span>报告保留（天）</span><input v-model.number="storage.report_retention_days" type="number" min="1" /></label>
        <label class="field"><span>运行记录保留（天）</span><input v-model.number="storage.run_record_retention_days" type="number" min="1" /></label>
        <label class="field"><span>日志保留（天）</span><input v-model.number="storage.log_retention_days" type="number" min="1" /></label>
        <label class="field"><span>最近数据保护（天）</span><input v-model.number="storage.protect_recent_days" type="number" min="1" /></label>
      </fieldset>
      <ButtonGroup class="editor-footer"><ActionButton type="submit" :disabled="busy || configLoading || configError" variant="primary" :loading="pendingAction === 'storage'">保存配置</ActionButton></ButtonGroup>
    </form>
    <div class="form-section settings-form manual-cleanup-section">
      <div class="form-section-head"><div><h2>手动清理</h2><p>选择内容和时间，确认后删除。</p></div></div>
      <div v-if="configLoading" class="loading-panel"><div class="skeleton"></div></div>
      <div v-else-if="configError" class="empty-state"><h3>清理选项加载失败</h3><p>请检查服务后重试。</p><ActionButton type="button" @click="loadUiConfig">重试</ActionButton></div>
      <div v-else class="manual-cleanup-layout">
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
    </div>
  </section>
  <section v-show="activeSection === 'security'" id="security-section" class="settings-section" role="tabpanel" aria-labelledby="security-tab">
    <div class="section-heading"><div><h2>安全设置</h2></div></div>
    <div class="security-panels">
    <div class="form-section settings-form">
      <div class="form-section-head"><div><h2>管理员账户</h2></div></div>
      <div class="account-action-list">
        <div class="account-action-row">
          <div class="account-action-copy"><span>管理员用户名</span><strong>{{ systemPrefs.admin_username || '—' }}</strong></div>
          <ActionButton type="button" variant="secondary" @click="openAccountModal('username')">修改用户名</ActionButton>
        </div>
        <div class="account-action-row">
          <div class="account-action-copy"><span>登录密码</span><strong>已设置</strong></div>
          <ActionButton type="button" variant="secondary" @click="openAccountModal('password')">修改密码</ActionButton>
        </div>
      </div>
    </div>
    <div class="form-section settings-form">
      <div class="form-section-head"><div><h2>访问保护</h2></div></div>
      <div class="account-security-options">
        <div class="mfa-switch-row">
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

        <label class="switch-row">
          <input v-model="swagger" type="checkbox" @change="updateDocs" />启用 API 接口文档
        </label>
      </div>

    </div>
    </div>
  </section>
  </div>

  <Transition name="modal-fade">
    <div v-if="accountModal" class="modal-backdrop" @click="closeAccountModal" @keydown.esc.stop="closeAccountModal">
      <div class="mfa-dialog-box account-dialog-box" role="dialog" aria-modal="true" aria-labelledby="account-dialog-title" @click.stop>
        <div class="mfa-dialog-header">
          <h3 id="account-dialog-title">{{ accountModal === 'username' ? '修改用户名' : '修改密码' }}</h3>
          <IconButton class="icon-button" type="button" aria-label="关闭" label="关闭" @click="closeAccountModal"><X :size="18" /></IconButton>
        </div>
        <form v-if="accountModal === 'username'" class="account-dialog-form" autocomplete="off" novalidate @submit.prevent="saveAdminUsername">
          <label class="field"><span>新用户名</span><input v-model="adminUsername.next" maxlength="32" autocomplete="off" placeholder="输入新用户名"></label>
          <label class="field"><span>当前密码</span><PasswordInput v-model="adminUsername.currentPassword" autocomplete="current-password" placeholder="输入当前密码" /></label>
          <p v-if="accountError" class="account-dialog-error" role="alert">{{ accountError }}</p>
          <p class="account-dialog-note">保存后需重新登录。</p>
          <ButtonGroup class="mfa-dialog-footer"><ActionButton type="button" variant="secondary" @click="closeAccountModal">取消</ActionButton><ActionButton type="submit" variant="primary" :disabled="usernameBusy" :loading="usernameBusy">确认修改</ActionButton></ButtonGroup>
        </form>
        <form v-else class="account-dialog-form" autocomplete="off" novalidate @submit.prevent="saveAdminPassword">
          <label class="field"><span>当前密码</span><PasswordInput v-model="adminPassword.current" autocomplete="current-password" placeholder="输入当前密码" /></label>
          <label class="field"><span>新密码</span><PasswordInput v-model="adminPassword.next" autocomplete="new-password" maxlength="128" placeholder="至少 12 位" /></label>
          <label class="field"><span>确认新密码</span><PasswordInput v-model="adminPassword.confirm" autocomplete="new-password" maxlength="128" placeholder="再次输入新密码" /></label>
          <p v-if="accountError" class="account-dialog-error" role="alert">{{ accountError }}</p>
          <p class="account-dialog-note">保存后需重新登录。</p>
          <ButtonGroup class="mfa-dialog-footer"><ActionButton type="button" variant="secondary" @click="closeAccountModal">取消</ActionButton><ActionButton type="submit" variant="primary" :disabled="passwordBusy" :loading="passwordBusy">确认修改</ActionButton></ButtonGroup>
        </form>
      </div>
    </div>
  </Transition>

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
          <div v-if="!isViewOnlyMfa || qrDataUrl" class="mfa-qr-center-box">
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
          <div v-if="mfaSecretText" class="mfa-dialog-manual-row">
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
            <label v-if="security.mfa_enabled" class="field">
              <span>当前设备验证码 <b>*</b></span>
              <input v-model="modalCurrentCode" class="mfa-dialog-code-input" inputmode="numeric" maxlength="6" autocomplete="one-time-code" />
            </label>
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
          <div v-else-if="!qrDataUrl" class="mfa-dialog-verify-section">
            <label class="field">
              <span>当前设备验证码 <b>*</b></span>
              <input v-model="modalCurrentCode" class="mfa-dialog-code-input" inputmode="numeric" maxlength="6" autocomplete="one-time-code" @keyup.enter="revealCurrentMfaQr" />
            </label>
          </div>
        </div>

        <ButtonGroup class="mfa-dialog-footer">
          <ActionButton type="button" @click="closeMfaModal" variant="secondary">
            {{ isViewOnlyMfa ? '关闭' : '取消' }}
          </ActionButton>
          <ActionButton
            v-if="isViewOnlyMfa && !qrDataUrl"
            type="button"
            :disabled="modalBusy || !/^\d{6}$/.test(modalCurrentCode.trim())"
            @click="revealCurrentMfaQr"
            variant="primary">查看二维码</ActionButton>
          <ActionButton
            v-if="!isViewOnlyMfa"

            type="button"
            :disabled="modalBusy || !/^\d{6}$/.test(modalVerifyCode.trim()) || (security.mfa_enabled && !/^\d{6}$/.test(modalCurrentCode.trim()))"
            @click="submitMfaBind"
           variant="primary">

            确认绑定
          </ActionButton>
        </ButtonGroup>
      </div>
    </div>
  </Transition>
  <Transition name="modal-fade">
    <div v-if="mfaDisableOpen" class="modal-backdrop" @click="mfaDisableOpen = false">
      <div class="mfa-dialog-box" role="dialog" aria-modal="true" @click.stop>
        <div class="mfa-dialog-header">
          <div class="mfa-dialog-title"><ShieldCheck :size="20" class="mfa-title-icon" /><h3>关闭两步验证（MFA）</h3></div>
          <IconButton class="icon-button" type="button" aria-label="关闭" @click="mfaDisableOpen = false" label="关闭"><X :size="18" /></IconButton>
        </div>
        <p class="mfa-dialog-intro">关闭后登录将只验证密码。请输入当前设备的动态验证码以确认。</p>
        <label class="field">
          <span>当前设备验证码 <b>*</b></span>
          <input v-model="mfaDisableCode" class="mfa-dialog-code-input" inputmode="numeric" maxlength="6" autocomplete="one-time-code" @keyup.enter="submitMfaDisable" />
        </label>
        <ButtonGroup class="mfa-dialog-footer">
          <ActionButton type="button" variant="secondary" @click="mfaDisableOpen = false">取消</ActionButton>
          <ActionButton type="button" variant="danger" :disabled="busy || !/^\d{6}$/.test(mfaDisableCode.trim())" @click="submitMfaDisable">确认关闭</ActionButton>
        </ButtonGroup>
      </div>
    </div>
  </Transition>
</template>

<style scoped>
.settings-page { width: min(100%, 1320px); margin-inline: auto; }
.settings-intro { margin-bottom: 22px; }
.settings-intro p { margin: 6px 0 0; color: #78909c; font-size: 12px; }
.settings-category-tabs { display: inline-flex; gap: 4px; margin: 0 0 30px; padding: 4px; border: 1px solid #d7e6eb; border-radius: 10px; background: rgba(235, 246, 249, .82); }
.settings-category-tabs button { min-width: 106px; padding: 10px 18px; border: 0; border-radius: 7px; color: #617d8b; font-size: 12px; font-weight: 650; }
.settings-category-tabs button:hover { background: rgba(255, 255, 255, .55); color: #1d6880; }
.settings-category-tabs button.active { background: #fff; color: #176b84; box-shadow: 0 1px 5px rgba(29, 87, 107, .13); }
.settings-section { margin-bottom: 0; }
.settings-section .section-heading { margin-bottom: 17px; }
.settings-section .settings-form { background: rgba(245, 251, 253, .88); border-color: #d6e8ed; border-radius: 12px; box-shadow: 0 8px 30px rgba(39, 101, 121, .045); }
.settings-section .form-section-head { margin-bottom: 16px; }
.settings-section .form-section-head h2 { margin: 0; font-size: 15px; }
.system-preferences-form { display: grid; gap: 22px; }
.system-preferences-form .form-grid { max-width: none; }
.system-preferences-form .field input { min-height: 42px; background: rgba(255, 255, 255, .94); }
.system-preferences-holiday { border-top: 1px solid #dce8ed; padding-top: 18px; }
.system-preferences-holiday h3 { margin: 0 0 16px; color: #294858; font-size: 14px; line-height: 1.35; }
.system-preferences-actions { display: flex; justify-content: flex-end; border-top: 1px solid #dce8ed; padding-top: 18px; }
.disk-status-card { display: grid; gap: 16px; margin-bottom: 18px !important; }
.disk-status-heading, .disk-status-heading > div, .disk-status-line, .disk-status-scale { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.disk-status-heading { border-bottom: 1px solid #dce8ed; padding-bottom: 15px; }
.disk-status-heading > div { justify-content: flex-start; color: #177991; }
.disk-status-heading strong { color: #263f4e; font-size: 15px; }
.disk-status-heading small { color: #718b98; font-size: 11px; }
.disk-status-badge { padding: 4px 9px; border-radius: 5px; background: #e5f5eb; color: #18835c; font-size: 11px; font-weight: 700; }
.disk-status-badge.warning { background: #fff0e8; color: #bd4a27; }
.disk-status-line strong { color: #138866; font-size: 26px; font-variant-numeric: tabular-nums; }
.disk-status-line span { color: #536c79; font-size: 12px; }
.disk-status-track { position: relative; height: 12px; border-radius: 9px; background: #dfe8ec; }
.disk-status-track span { display: block; height: 100%; border-radius: inherit; background: linear-gradient(90deg, #37c995, #14ac79); }
.disk-status-track i { position: absolute; top: -3px; width: 3px; height: 18px; border-radius: 2px; background: #d44e40; }
.disk-status-scale { margin-top: -10px; color: #718b98; font-size: 10px; }
.disk-status-scale span:nth-child(2) { color: #c44737; }
.disk-alert-form { display: grid; grid-template-columns: minmax(180px, 1fr) 140px minmax(180px, 1fr) auto; align-items: end; gap: 14px; border-top: 1px solid #dce8ed; padding-top: 17px; }
.disk-alert-form .switch-row { min-height: 40px; }
.disk-alert-form .field { margin: 0; }
.storage-summary { gap: 12px; margin-bottom: 18px; }
.storage-summary > div { background: rgba(245, 251, 253, .88); border-color: #d6e8ed; border-radius: 10px; padding: 16px 19px; }
.storage-summary strong { font-variant-numeric: tabular-nums; }
.storage-panels { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.12fr); gap: 18px; align-items: start; }
.storage-panels > .settings-form { min-width: 0; margin-bottom: 0; }
.storage-panels .manual-cleanup-layout { grid-template-columns: minmax(0, 1fr); gap: 20px; }
.storage-panels .manual-cleanup-scopes { grid-template-columns: minmax(0, 1fr); }
.storage-panels .auto-cleanup-fields { gap: 15px; }
.storage-panels .editor-footer { margin-top: 7px; }
.security-panels { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; align-items: stretch; }
.security-panels > .settings-form { min-width: 0; margin-bottom: 0; }
.account-action-list { display: grid; }
.account-action-row { display: flex; align-items: center; justify-content: space-between; gap: 20px; min-height: 70px; padding: 12px 0; border-bottom: 1px solid #dce8ed; }
.account-action-row:first-child { padding-top: 0; }
.account-action-row:last-child { border-bottom: 0; padding-bottom: 0; }
.account-action-copy { display: grid; gap: 4px; min-width: 0; }
.account-action-copy span { color: #708996; font-size: 11px; font-weight: 600; }
.account-action-copy strong { color: #263f4e; font-size: 14px; font-weight: 700; overflow-wrap: anywhere; }
.account-security-options { display: grid; gap: 0; padding-top: 0; }
.account-security-options > * { display: flex; align-items: center; min-height: 70px; border-bottom: 1px solid #dce8ed; }
.account-security-options > *:last-child { border-bottom: 0; }
.account-security-options .switch-row { font-size: 12px; }
.account-dialog-box { max-width: 440px; gap: 19px; }
.account-dialog-box h3 { margin: 0; color: #253f4e; font-size: 18px; }
.account-dialog-form { display: grid; gap: 15px; }
.account-dialog-form .field { font-size: 12px; }
.account-dialog-note { margin: 0; color: #8198a4; font-size: 11px; }
.account-dialog-error { margin: 0; color: #b42318; font-size: 12px; }
.account-dialog-form .mfa-dialog-footer { margin-top: 3px; }
@media (max-width: 1080px) { .storage-panels, .security-panels { grid-template-columns: 1fr; } }
@media (max-width: 1000px) { .disk-alert-form { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 680px) { .system-preferences-form { gap: 19px; } .settings-category-tabs { display: flex; } .settings-category-tabs button { min-width: 0; flex: 1; padding-inline: 8px; } }
</style>
