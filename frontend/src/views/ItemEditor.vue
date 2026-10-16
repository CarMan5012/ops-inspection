<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { ArrowLeft, Info, CheckCircle2, Plus, Minus } from '../icons'
import { api, type Row } from '../api'
import { attempt, notify } from '../ui'
import GrafanaOptions from '../components/GrafanaOptions.vue'

const route = useRoute()
const router = useRouter()
const jobId = computed(() => Number(route.params.jobId))
const id = computed(() => Number(route.params.id || 0))
const saving = ref(false)
const loading = ref(true)
const loadFailed = ref(false)
const authProfiles = ref<Row[]>([])
const taskbarTemplates = ref<Row[]>([])
const selectedTaskbar = computed(() => taskbarTemplates.value.find(template => template.key === form.taskbar_template_key))
const sections = ref<string[]>([])
const watermarkTimeFormats = ref<{ id: string; label: string }[]>([])
const templateSelected = ref(false)
const sectionMode = ref<'template' | 'custom'>('template')
const selectedTemplateSection = ref('')
const customSection = ref('')
const customInputRef = ref<HTMLInputElement | null>(null)
const advanced = ref(false)
const checkingPage = ref(false)
const dashboardQuery = ref('')
const dashboardOptions = ref<Row[]>([])
const dashboardsLoaded = ref(false)
const dashboardsLoading = ref(false)
const showDashboardPicker = ref(false)
const selectedDashboardTitle = ref('')
const selectedDashboardUid = computed(() => {
  try { return new URL(String(form.url || '')).pathname.match(/\/d(?:-solo)?\/([^/]+)/)?.[1] || '' }
  catch { return '' }
})
const selectionError = ref('')
let dashboardRequestId = 0
const form = reactive<Row>({
  name: '', item_type: 'grafana', url: '', section: '巡检截图', auth_profile_id: '',
  enabled: true, real_browser_capture: true, taskbar_template_key: '', capture_mode: 'viewport', css_selector: '',
  wait_selector: '', wait_seconds: 3, timeout_seconds: 120, total_timeout_seconds: 240, retry_count: 2,
  browser_width: '', browser_height: '', sort_order: 100, taskbar_enabled: true,
  watermark_enabled: false, watermark_time_enabled: true, watermark_time_format: '', watermark_text: '', watermark_opacity: 65,
  watermark_font_size: 24, watermark_gap_x: 140, watermark_gap_y: 140, watermark_angle: -45,
})
function bool(value: unknown): boolean { return value === true || value === 1 || value === '1' }

function switchToTemplate() {
  sectionMode.value = 'template'
  if (!selectedTemplateSection.value || selectedTemplateSection.value === '__custom__') {
    selectedTemplateSection.value = sections.value[0] || ''
  }
}

function switchToCustom() {
  sectionMode.value = 'custom'
  if (!customSection.value && selectedTemplateSection.value && selectedTemplateSection.value !== '__custom__') {
    customSection.value = selectedTemplateSection.value
  }
  nextTick(() => {
    customInputRef.value?.focus()
  })
}

function onSectionSelectChange() {
  if (selectedTemplateSection.value === '__custom__') {
    switchToCustom()
  }
}

onMounted(async () => {
  const [data, taskbarData] = await Promise.all([
    attempt(() => api<Row>(`/jobs/${jobId.value}`)),
    attempt(() => api<Row>('/taskbar-templates')),
  ])
  if (!data || (id.value && !(data.items || []).some((row: Row) => Number(row.id) === id.value))) {
    loadFailed.value = true
    loading.value = false
    return
  }
  authProfiles.value = data?.auth_profiles || []
  taskbarTemplates.value = taskbarData?.templates || []
  sections.value = data?.template_sections || []
  watermarkTimeFormats.value = data?.watermark_time_formats || []
  Object.assign(form, data?.item_defaults || {})
  templateSelected.value = Boolean(data?.job?.template_id)
  if (id.value) {
    const item = (data?.items || []).find((row: Row) => Number(row.id) === id.value)
    if (item) {
      Object.assign(form, item)
      form.auth_profile_id = item.auth_profile_id ?? ''
      if (item.item_type === 'grafana' && item.url) selectedDashboardTitle.value = String(item.name || '当前看板')
      for (const key of ['enabled', 'real_browser_capture', 'taskbar_enabled', 'watermark_enabled', 'watermark_time_enabled']) form[key] = bool(form[key])

      const curSection = String(item.section || '').trim()
      if (sections.value.length > 0 && sections.value.includes(curSection)) {
        sectionMode.value = 'template'
        selectedTemplateSection.value = curSection
        customSection.value = ''
      } else {
        sectionMode.value = 'custom'
        customSection.value = curSection || '巡检截图'
        selectedTemplateSection.value = sections.value[0] || ''
      }
    }
  } else {
    if (sections.value.length > 0) {
      sectionMode.value = 'template'
      selectedTemplateSection.value = sections.value[0]
      customSection.value = ''
    } else {
      sectionMode.value = 'custom'
      customSection.value = '巡检截图'
      selectedTemplateSection.value = ''
    }
  }
  loading.value = false
})
async function save() {
  selectionError.value = ''
  if (form.item_type === 'grafana' && (!form.auth_profile_id || !form.url)) {
    selectionError.value = '请先选择网站连接和 Grafana 看板'
    return
  }
  saving.value = true
  const finalSection = (
    sectionMode.value === 'custom'
      ? customSection.value.trim()
      : selectedTemplateSection.value
  ) || '巡检截图'
  form.section = finalSection
  const payload = { ...form, auth_profile_id: form.auth_profile_id || null, browser_width: form.browser_width || null, browser_height: form.browser_height || null }
  const result = await attempt(() => api(id.value ? `/items/${id.value}` : `/jobs/${jobId.value}/items`, id.value ? 'PUT' : 'POST', payload), id.value ? '截图项已更新' : '截图项已创建')
  saving.value = false
  if (result) router.push(`/jobs/${jobId.value}`)
}

async function loadDashboards() {
  if (!form.auth_profile_id) {
    dashboardsLoading.value = false
    selectionError.value = '请先选择网站连接'
    return
  }
  dashboardsLoading.value = true
  dashboardsLoaded.value = false
  selectionError.value = ''
  const requestId = ++dashboardRequestId
  const profileId = form.auth_profile_id
  const query = dashboardQuery.value
  const result = await attempt(() => api<Row>(`/auth-profiles/${profileId}/dashboards?query=${encodeURIComponent(query)}`))
  if (requestId !== dashboardRequestId || profileId !== form.auth_profile_id) return
  dashboardOptions.value = result?.dashboards || []
  dashboardsLoaded.value = Boolean(result)
  dashboardsLoading.value = false
}

async function onConnectionChange() {
  dashboardRequestId++
  dashboardsLoading.value = false
  form.url = ''
  selectedDashboardTitle.value = ''
  dashboardOptions.value = []
  dashboardsLoaded.value = false
  showDashboardPicker.value = true
  if (form.auth_profile_id) await loadDashboards()
}

function chooseDashboard(dashboard: Row) {
  showDashboardPicker.value = false
  if (selectedDashboardUid.value === String(dashboard.uid)) return
  form.url = String(dashboard.url)
  form.name = String(dashboard.title || 'Grafana 看板')
  selectedDashboardTitle.value = String(dashboard.title || 'Grafana 看板')
  selectionError.value = ''
}

async function toggleDashboardPicker() {
  showDashboardPicker.value = !showDashboardPicker.value
  if (showDashboardPicker.value && !dashboardsLoaded.value && form.auth_profile_id) await loadDashboards()
}

function onPageTypeChange() {
  dashboardRequestId++
  dashboardsLoading.value = false
  selectionError.value = ''
  if (form.item_type === 'grafana' && form.url && !/\/d(?:-solo)?\//.test(String(form.url))) form.url = ''
  if (form.item_type === 'grafana' && form.auth_profile_id) void loadDashboards()
}

async function checkPage() {
  if (checkingPage.value || !String(form.url || '').trim()) return
  checkingPage.value = true
  try {
    const result = await api<Row>('/connections/check-target', 'POST', {
      url: String(form.url || '').trim(), auth_profile_id: form.auth_profile_id || null,
    })
    if (!result.ok) notify(String(result.message || '页面验证失败'), 'error')
    else if (result.monitor_status === 'unknown') notify(`页面可访问；告警无法判断${result.monitor_message ? `：${result.monitor_message}` : ''}`, 'info')
    else notify('页面可访问，登录有效')
  } catch (cause) {
    notify(cause instanceof Error ? cause.message : '页面验证失败', 'error')
  } finally {
    checkingPage.value = false
  }
}
</script>

<template>
  <div class="editor-top"><RouterLink :to="`/jobs/${jobId}`" class="back-link"><ArrowLeft :size="16" />返回</RouterLink></div>
  <section class="page-intro editor-intro"><div><h1>{{ id ? '编辑截图项' : '新增截图项' }}</h1></div></section>
  <div v-if="loading" class="loading-panel"><div class="skeleton" v-for="n in 4" :key="n"></div></div>
  <div v-else-if="loadFailed" class="empty-state"><h3>截图项加载失败</h3><p>请返回任务详情，确认截图项仍然存在后重试。</p><ActionButton :to="`/jobs/${jobId}`"  variant="secondary">返回</ActionButton></div>
  <form v-else class="editor-layout item-editor-layout" @submit.prevent="save"><div class="editor-main">
    <section class="form-section"><div class="form-section-head"><div><h2>页面信息</h2></div></div><div class="form-grid">
      <label class="field"><span>截图名称 <b>*</b></span><input v-model="form.name" required placeholder="例如：生产看板" /></label>
      <label class="field"><span>页面类型</span><select v-model="form.item_type" @change="onPageTypeChange"><option value="grafana">Grafana 看板</option><option value="kibana">Kibana 仪表盘</option><option value="web">普通网页</option></select></label>
      <div v-if="form.item_type === 'grafana'" class="field field-full dashboard-select-field">
        <span>选择 Grafana 看板 <b>*</b></span>
        <div class="dashboard-select-header"><select v-model="form.auth_profile_id" aria-label="选择网站连接" @change="onConnectionChange"><option value="">先选择网站连接</option><option v-for="profile in authProfiles" :key="profile.id" :value="profile.id">{{ profile.name }}</option></select><RouterLink to="/auth" class="connection-link">管理连接</RouterLink></div>
        <div v-if="form.url" class="dashboard-selected"><CheckCircle2 :size="19" class="dashboard-selected-icon" /><div class="dashboard-selected-copy"><strong>{{ selectedDashboardTitle || form.name || '当前看板' }}</strong><small>截图将使用这个看板</small></div><ActionButton type="button"  class="dashboard-change-button" :aria-expanded="showDashboardPicker" @click="toggleDashboardPicker" variant="secondary">{{ showDashboardPicker ? '收起看板' : '更换看板' }}</ActionButton></div>
        <div v-if="form.auth_profile_id && (!form.url || showDashboardPicker)" class="dashboard-search-tools"><input v-model="dashboardQuery" placeholder="搜索看板名称" @keydown.enter.prevent="loadDashboards" /><ActionButton type="button" :disabled="dashboardsLoading" @click="loadDashboards" variant="secondary" :loading="dashboardsLoading">搜索看板</ActionButton></div>
        <div v-if="(!form.url || showDashboardPicker) && dashboardOptions.length" class="dashboard-picker dashboard-single-picker">
          <button v-for="dashboard in dashboardOptions" :key="dashboard.uid" type="button" class="dashboard-select-option" :class="{ selected: selectedDashboardUid === dashboard.uid }" @click="chooseDashboard(dashboard)"><span><strong>{{ dashboard.title }}</strong><small>{{ dashboard.folder || 'Grafana 看板' }}</small></span><span class="dashboard-option-mark">{{ selectedDashboardUid === dashboard.uid ? '已选' : '选择' }}</span></button>
        </div>
        <p v-else-if="(!form.url || showDashboardPicker) && dashboardsLoading" class="connection-hint">正在读取看板…</p>
        <p v-else-if="(!form.url || showDashboardPicker) && dashboardsLoaded" class="connection-hint">没有找到看板，请换个名称搜索。</p>
        <p v-else-if="!form.auth_profile_id" class="connection-hint">先选择连接，再选择看板。</p>
        <p v-if="selectionError" class="dashboard-selection-error" role="alert">{{ selectionError }}</p>
        <GrafanaOptions v-if="form.url && form.auth_profile_id" :url="String(form.url)" :auth-profile-id="form.auth_profile_id" @update:url="form.url = $event" />
      </div>
      <label v-else class="field field-full"><span>页面地址 <b>*</b></span><input v-model="form.url" type="url" required placeholder="https://example.com/dashboard" /></label>
      <div class="field report-section-field">
        <div class="field-title-line">
          <span>报告章节 <b>*</b></span>
          <div v-if="sections.length" class="section-mode-toggle" role="group" aria-label="章节模式切换">
            <button
              type="button"
              class="mode-btn"
              :class="{ active: sectionMode === 'template' }"
              @click="switchToTemplate"
            >模板章节</button>
            <button
              type="button"
              class="mode-btn"
              :class="{ active: sectionMode === 'custom' }"
              @click="switchToCustom"
            >自定义章节</button>
          </div>
        </div>
        <template v-if="sectionMode === 'template' && sections.length">
          <select v-model="selectedTemplateSection" class="section-select" @change="onSectionSelectChange">
            <option v-for="section in sections" :key="section" :value="section">{{ section }}</option>
            <option value="__custom__">自定义写入新章节...</option>
          </select>
          <small>截图将放入所选报告章节。</small>
        </template>
        <template v-else>
          <input
            ref="customInputRef"
            v-model="customSection"
            class="section-input"
            required
            placeholder="请输入自定义章节名称，例如：网络设备专区"
          />
          <small class="custom-hint-text">{{ templateSelected ? '自定义章节不在所选模板中时，报告生成会失败；请先在任务中切换为自动排版。' : '自定义章节将由系统自动排版。' }}</small>
        </template>
      </div>
      <label v-if="form.item_type !== 'grafana'" class="field"><span>网站连接</span><select v-model="form.auth_profile_id"><option value="">无需登录</option><option v-for="profile in authProfiles" :key="profile.id" :value="profile.id">{{ profile.name }}</option></select><small>每个页面可选择不同账号。</small></label>
    </div><div class="item-page-check-actions"><ActionButton type="button" :disabled="checkingPage || !form.url" :aria-busy="checkingPage" @click="checkPage" variant="primary" :loading="checkingPage">验证页面</ActionButton></div></section>
    <section class="form-section"><div class="form-section-head"><div><h2>截图方式</h2></div></div><div class="form-grid">
      <label class="switch-row field-full"><input v-model="form.real_browser_capture" type="checkbox" /><span><strong>完整浏览器截图</strong><small>包含标签页、地址栏和窗口按钮。</small></span></label>
      <label v-if="!form.real_browser_capture" class="field"><span>网页截图范围</span><select v-model="form.capture_mode"><option value="viewport">可见区域</option><option value="full_page">整个网页</option><option value="selector">指定元素</option></select></label>
      <label v-if="!form.real_browser_capture && form.capture_mode === 'selector'" class="field"><span>元素选择器 <b>*</b></span><input v-model="form.css_selector" required placeholder="#chart-panel" /></label>
      <label class="switch-row field-full"><input v-model="form.taskbar_enabled" type="checkbox" /><span><strong>显示 Windows 风格任务栏</strong><small>完整浏览器截图时，任务栏包含在图片尺寸内。</small></span></label>
      <label v-if="form.taskbar_enabled" class="field field-full"><span>底栏图片</span><select v-model="form.taskbar_template_key"><option value="">自动轮换（同一次巡检统一底栏）</option><option v-for="template in taskbarTemplates" :key="template.key" :value="template.key">{{ template.name }}{{ template.kind === 'builtin' ? '（预置）' : '' }}</option></select><small>自动轮换是默认设置：同一次巡检的截图项共用一张预置底栏，不同巡检可能切换。手动选中图片后，该截图项固定使用所选底栏。<RouterLink to="/templates?tab=taskbar">上传和管理底栏</RouterLink></small><img v-if="selectedTaskbar" :src="selectedTaskbar.image_url" :alt="selectedTaskbar.name" class="taskbar-choice-preview" /></label>
    </div></section>
    <section class="form-section"><div class="form-section-head"><div><h2>等待与重试</h2></div></div><div class="form-grid">
      <label class="field"><span>截图前等待（秒）</span><input v-model.number="form.wait_seconds" type="number" step="0.5" min="0" required /></label>
      <label class="field"><span>失败重试次数</span><input v-model.number="form.retry_count" type="number" min="0" max="10" required /></label>
      <label class="field"><span>页面截图超时（秒）</span><input v-model.number="form.timeout_seconds" type="number" min="1" required /><small>单次截图超过此时间则重新尝试。</small></label>
      <label class="field"><span>截图总时限（秒）</span><input v-model.number="form.total_timeout_seconds" type="number" min="1" required /><small>包含登录、截图和所有重试。</small></label>
    </div></section>
    <section class="form-section"><button class="advanced-toggle" type="button" :aria-expanded="advanced" @click="advanced = !advanced">高级设置 <Minus v-if="advanced" :size="15" /><Plus v-else :size="15" /></button><div v-if="advanced" class="form-grid advanced-fields">
      <label class="field field-full"><span>等待元素</span><input v-model="form.wait_selector" placeholder="例如：.dashboard-container" /><small>页面中出现指定元素后再截图。无需设置时留空。</small></label>
      <label class="field"><span>单独设置宽度（px）</span><input v-model.number="form.browser_width" type="number" min="800" placeholder="沿用任务设置" /></label><label class="field"><span>单独设置高度（px）</span><input v-model.number="form.browser_height" type="number" min="600" placeholder="沿用任务设置" /></label>
      <label class="field"><span>排序位置</span><input v-model.number="form.sort_order" type="number" /></label>
      <label class="switch-row field-full"><input v-model="form.watermark_enabled" type="checkbox" /><strong>启用截屏水印</strong></label>
      <template v-if="form.watermark_enabled"><label class="switch-row field-full"><input v-model="form.watermark_time_enabled" type="checkbox" /><strong>启用时间水印</strong></label><label v-if="form.watermark_time_enabled" class="field field-full"><span>时间格式</span><select v-model="form.watermark_time_format"><option v-for="option in watermarkTimeFormats" :key="option.id" :value="option.id">{{ option.label }}</option></select></label><label class="field field-full"><span>自定义水印内容</span><textarea v-model="form.watermark_text" rows="3" /><small>可留空，仅显示时间水印。</small></label><label class="field"><span>透明度</span><input v-model.number="form.watermark_opacity" type="number" min="0" max="255" /></label><label class="field"><span>字号</span><input v-model.number="form.watermark_font_size" type="number" min="10" max="72" /></label><label class="field"><span>横向间距</span><input v-model.number="form.watermark_gap_x" type="number" min="20" max="800" /></label><label class="field"><span>纵向间距</span><input v-model.number="form.watermark_gap_y" type="number" min="20" max="800" /></label><label class="field"><span>倾斜角度</span><input v-model.number="form.watermark_angle" type="number" min="-90" max="90" /></label></template>
    </div></section>
    <ButtonGroup class="editor-footer"><ActionButton :to="`/jobs/${jobId}`" variant="secondary">取消</ActionButton><ActionButton type="submit" :disabled="saving" variant="primary" :loading="saving">保存截图</ActionButton></ButtonGroup>
  </div><aside class="editor-aside"><div class="aside-card"><Info :size="20" /><h3>截图建议</h3><p>先保存截图项，再点击“测试”确认页面、登录和截图结果都符合预期。</p></div><div class="aside-card soft"><label class="switch-row"><input v-model="form.enabled" type="checkbox" /><strong>启用截图项</strong></label><p>关闭后，任务运行时会跳过这一项。</p></div></aside></form>
</template>

<style scoped>
.taskbar-choice-preview{display:block;width:100%;max-width:850px;height:auto;margin-top:10px;border:1px solid #dce4e8;background:#eef2f4}
</style>
