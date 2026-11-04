<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { ArrowLeft, Trash2, CircleCheck } from '../icons'
import { api, type Row } from '../api'
import { attempt } from '../ui'

type PageRow = {
  key: number
  name: string
  url: string
  section: string
  auth_profile_id: number | ''
  checked: boolean | null
  checkMessage: string
  checkVersion: number
}

const route = useRoute()
const router = useRouter()
const jobId = computed(() => Number(route.params.id))
const profiles = ref<Row[]>([])
const sections = ref<string[]>([])
const rows = ref<PageRow[]>([])
const dashboards = ref<Row[]>([])
const selected = ref<string[]>([])
const connectionId = ref<number | ''>('')
const searchText = ref('')
const pastedUrls = ref('')
const error = ref('')
const loading = ref(true)
const searching = ref(false)
const testing = ref(false)
const saving = ref(false)
let nextKey = 1
let dashboardRequestId = 0
const dashboardConnectionId = ref<number | ''>('')

watch([connectionId, searchText], () => {
  dashboardRequestId++
  dashboards.value = []
  selected.value = []
  dashboardConnectionId.value = ''
  searching.value = false
}, { flush: 'sync' })

function origin(raw: string): string {
  try { return new URL(raw).origin } catch { return '' }
}

function suggestedProfile(url: string): number | '' {
  const matches = profiles.value.filter(profile => origin(String(profile.login_url || '')) === origin(url))
  return matches.length === 1 ? Number(matches[0].id) : ''
}

function defaultName(url: string): string {
  try {
    const parts = new URL(url).pathname.split('/').filter(Boolean)
    return decodeURIComponent(parts.at(-1) || '网页截图').replace(/[-_]/g, ' ')
  } catch { return '网页截图' }
}

function addRow(url: string, name = '', authId: number | '' = ''): void {
  if (testing.value || saving.value) return
  const clean = url.trim()
  const assigned = authId || suggestedProfile(clean)
  if (!clean || rows.value.some(row => row.url === clean && row.auth_profile_id === assigned)) return
  rows.value.push({
    key: nextKey++, name: name || defaultName(clean), url: clean,
    section: sections.value[0] || '巡检截图',
    auth_profile_id: assigned, checked: null, checkMessage: '', checkVersion: 0,
  })
}

async function loadDashboards(): Promise<void> {
  if (testing.value || saving.value) return
  if (!connectionId.value) { error.value = '请先选择网站连接'; return }
  const requestedConnectionId = connectionId.value
  const requestedQuery = searchText.value
  const requestId = ++dashboardRequestId
  searching.value = true
  error.value = ''
  try {
    const result = await api<Row>(`/auth-profiles/${requestedConnectionId}/dashboards?query=${encodeURIComponent(requestedQuery)}`)
    if (requestId !== dashboardRequestId || connectionId.value !== requestedConnectionId || searchText.value !== requestedQuery) return
    dashboards.value = result.dashboards || []
    dashboardConnectionId.value = requestedConnectionId
    selected.value = []
  } catch (cause) {
    if (requestId !== dashboardRequestId || connectionId.value !== requestedConnectionId || searchText.value !== requestedQuery) return
    error.value = cause instanceof Error ? cause.message : '查找看板失败'
    dashboards.value = []
    dashboardConnectionId.value = ''
  } finally {
    if (requestId === dashboardRequestId) searching.value = false
  }
}

function addSelected(): void {
  if (testing.value || saving.value) return
  if (!connectionId.value || dashboardConnectionId.value !== connectionId.value) {
    error.value = '网站连接已变化，请重新查找看板'
    return
  }
  const chosen = dashboards.value.filter(row => selected.value.includes(String(row.uid)))
  for (const dashboard of chosen) addRow(String(dashboard.url), String(dashboard.title), dashboardConnectionId.value)
  selected.value = []
}

function addPasted(): void {
  if (testing.value || saving.value) return
  error.value = ''
  const urls = pastedUrls.value.split(/\r?\n/).map(value => value.trim()).filter(Boolean)
  const invalid = urls.find(value => { try { return !['http:', 'https:'].includes(new URL(value).protocol) } catch { return true } })
  if (invalid) { error.value = `地址格式不正确：${invalid.slice(0, 90)}`; return }
  for (const url of urls) addRow(url)
  pastedUrls.value = ''
}

function rowFingerprint(row: PageRow): string {
  return JSON.stringify([row.key, row.name, row.url, row.section, row.auth_profile_id])
}

function invalidateCheck(row: PageRow): void {
  row.checkVersion++
  row.checked = null
  row.checkMessage = ''
}

async function check(row: PageRow): Promise<boolean> {
  const version = ++row.checkVersion
  const fingerprint = rowFingerprint(row)
  const url = row.url
  const authProfileId = row.auth_profile_id || null
  const isCurrent = () => row.checkVersion === version && rowFingerprint(row) === fingerprint && rows.value.includes(row)
  row.checked = null
  row.checkMessage = '正在检查…'
  try {
    const result = await api<Row>('/connections/check-target', 'POST', {
      url, auth_profile_id: authProfileId,
    })
    if (!isCurrent()) return false
    row.checked = Boolean(result.ok)
    row.checkMessage = result.ok
      ? result.monitor_status === 'alert' ? '页面可访问 · 发现告警'
        : result.monitor_status === 'unknown' ? `页面可访问 · 告警无法判断${result.monitor_message ? `：${result.monitor_message}` : ''}`
          : '页面可访问'
      : String(result.message || '页面无法访问')
  } catch (cause) {
    if (!isCurrent()) return false
    row.checked = false
    row.checkMessage = cause instanceof Error ? cause.message : '检查失败'
  }
  return row.checked === true
}

async function checkAll(): Promise<boolean> {
  testing.value = true
  error.value = ''
  const checkingRows = [...rows.value]
  const fingerprints = checkingRows.map(rowFingerprint)
  try {
    let passed = true
    for (const row of checkingRows) if (!(await check(row))) passed = false
    const unchanged = rows.value.length === checkingRows.length && checkingRows.every((row, index) =>
      rows.value[index] === row && rowFingerprint(row) === fingerprints[index])
    if (!unchanged) error.value = '页面配置在检查期间发生变化，请重新检查'
    else if (!passed) error.value = '有页面未通过检查，请核对地址和对应的连接'
    return passed && unchanged
  } finally {
    testing.value = false
  }
}

async function save(): Promise<void> {
  if (saving.value || testing.value) return
  if (!rows.value.length) { error.value = '请先添加页面'; return }
  if (rows.value.some(row => !row.name.trim())) { error.value = '请填写每个页面的截图名称'; return }
  saving.value = true
  try {
    const passed = await checkAll()
    if (!passed) return
    const items = rows.value.map(row => ({
        name: row.name.trim(), url: row.url.trim(), section: row.section.trim(),
        auth_profile_id: row.auth_profile_id || null, real_browser_capture: true,
      }))
    const result = await attempt(() => api<Row>(`/jobs/${jobId.value}/items/batch`, 'POST', { items }), `已添加 ${items.length} 个页面`)
    if (result?.ok) router.push(`/jobs/${jobId.value}`)
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  const data = await attempt(() => api<Row>(`/jobs/${jobId.value}`))
  profiles.value = data?.auth_profiles || []
  sections.value = data?.template_sections || []
  loading.value = false
})
</script>

<template>
  <div class="editor-top"><RouterLink :to="`/jobs/${jobId}`" class="back-link"><ArrowLeft :size="16" />返回</RouterLink></div>
  <section class="page-intro editor-intro"><div><h1>批量添加页面</h1><p>一个任务可以包含多个页面，每个页面使用自己的登录连接。</p></div></section>
  <div v-if="loading" class="loading-panel"><div class="skeleton" v-for="n in 3" :key="n"></div></div>
  <template v-else>
    <div v-if="error" class="schedule-warning" role="alert">{{ error }}</div>
    <div class="connection-grid">
      <section class="form-section">
        <div class="form-section-head"><div><h2>从 Grafana 选择</h2><p>登录后显示当前账号能访问的看板。</p></div></div>
        <div class="connection-search-row">
          <select v-model="connectionId" :disabled="testing || saving" aria-label="选择网站连接"><option value="">选择网站连接</option><option v-for="profile in profiles" :key="profile.id" :value="Number(profile.id)">{{ profile.name }}</option></select>
          <input v-model="searchText" :disabled="testing || saving" placeholder="搜索看板名称" @keydown.enter.prevent="loadDashboards" />
          <ActionButton type="button" :disabled="searching || testing || saving || !connectionId" @click="loadDashboards" variant="secondary">查找看板</ActionButton>
        </div>
        <div v-if="dashboards.length" class="dashboard-picker">
          <label v-for="dashboard in dashboards" :key="dashboard.uid" class="dashboard-choice">
            <input v-model="selected" type="checkbox" :disabled="testing || saving" :value="String(dashboard.uid)" />
            <span><strong>{{ dashboard.title }}</strong><small>{{ dashboard.folder || 'Grafana 看板' }}</small></span>
          </label>
        </div>
        <p v-else class="connection-hint">{{ searching ? '正在读取看板…' : '选择连接并点击“查找看板”，或在右侧直接粘贴链接。' }}</p>
        <ActionButton v-if="dashboards.length" type="button" :disabled="!selected.length || testing || saving" @click="addSelected" variant="secondary">导入所选{{ selected.length ? `（${selected.length}）` : '' }}</ActionButton>
      </section>
      <section class="form-section">
        <div class="form-section-head"><div><h2>粘贴页面地址</h2><p>每行一个 URL，适合已有看板链接。</p></div></div>
        <textarea v-model="pastedUrls" :disabled="testing || saving" class="connection-paste" rows="7" placeholder="https://grafana.example.com/d/abc/overview&#10;https://grafana.example.com/d/def/resources" />
        <div class="connection-paste-actions"><RouterLink to="/auth" class="connection-link">管理连接</RouterLink><ActionButton type="button" :disabled="testing || saving" @click="addPasted" variant="secondary">添加链接</ActionButton></div>
      </section>
    </div>
    <section class="form-section connection-review">
      <div class="form-section-head"><div><h2>确认页面与连接</h2><p>同一 Grafana 地址有多个账号时，请为每行明确选择。</p></div><span class="count-label">{{ rows.length }} 项</span></div>
      <div v-if="!rows.length" class="connection-empty">还没有页面。可从 Grafana 勾选，也可粘贴多个链接。</div>
      <div v-for="row in rows" :key="row.key" class="connection-row">
        <div class="connection-row-main">
          <label class="field"><span>截图名称</span><input v-model="row.name" :disabled="testing || saving" @input="invalidateCheck(row)" /></label>
          <label class="field"><span>页面地址</span><input v-model="row.url" type="url" :disabled="testing || saving" @input="invalidateCheck(row)" /></label>
          <label class="field"><span>网站连接</span><select v-model="row.auth_profile_id" :disabled="testing || saving" @change="invalidateCheck(row)"><option value="">无需登录 / 待选择</option><option v-for="profile in profiles" :key="profile.id" :value="Number(profile.id)">{{ profile.name }}</option></select></label>
          <label class="field"><span>报告章节</span><select v-if="sections.length" v-model="row.section" :disabled="testing || saving" @change="invalidateCheck(row)"><option v-for="section in sections" :key="section" :value="section">{{ section }}</option></select><input v-else v-model="row.section" :disabled="testing || saving" @input="invalidateCheck(row)" /></label>
        </div>
        <div class="connection-row-bottom"><span class="connection-row-url">{{ row.url }}</span><span v-if="row.checkMessage" class="connection-check" :class="row.checked === false ? 'is-error' : row.checked ? 'is-ok' : ''">{{ row.checkMessage }}</span><div class="connection-row-actions"><IconButton type="button" class="table-icon-action" :disabled="testing || saving" title="检查页面" aria-label="检查页面" @click="check(row)" label="检查页面"><CircleCheck :size="16" /></IconButton><IconButton type="button" class="table-icon-action danger-hover" :disabled="testing || saving" title="移除页面" aria-label="移除页面" @click="rows = rows.filter(item => item.key !== row.key)" label="移除页面"><Trash2 :size="16" /></IconButton></div></div>
      </div>
      <ButtonGroup class="connection-footer"><ActionButton type="button"  :disabled="!rows.length || testing || saving" @click="checkAll" variant="secondary" :loading="testing">全部检查</ActionButton><ActionButton type="button"  :disabled="!rows.length || testing || saving" @click="save" variant="primary" :loading="saving">批量保存</ActionButton></ButtonGroup>
    </section>
  </template>
</template>
